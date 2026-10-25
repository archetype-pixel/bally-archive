// lab-research: builds a research dossier from the archive (lab_dossier RPC) and has Google Gemini write a cited brief.
// Runs AS THE SIGNED-IN STAFF USER with header x-bally-surface: studio (Collection tables refuse writes from it).
// POST { question, project_id? }  ->  { id, status, brief, dossier, model }
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const SB_URL = Deno.env.get("SUPABASE_URL")!;
const ANON = Deno.env.get("SUPABASE_ANON_KEY")!;
const GEMINI = Deno.env.get("GEMINI_API_KEY") ?? "";
const MODELS = (Deno.env.get("GEMINI_TEXT_MODELS") ?? "v1beta/gemini-3.8-pro,v1beta/gemini-3.8-flash,v1/gemini-3.8-flash").split(",");
const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-bally-surface", "Access-Control-Allow-Methods": "POST, OPTIONS" };
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { ...CORS, "Content-Type": "application/json" } });

const SYSTEM = `You are the research lead of the Bally Heritage Archive (Bally, the Swiss luxury house founded 1851 in Schoenenwerd).
You write for Bally's design and product team. You are given a DOSSIER: facts computed from the archive database for one research question.
Write a research brief in Markdown, in plain English, with no em dashes. Use ONLY the dossier. Never invent objects, dates, people, prices or sources.
Cite every specific claim: archive objects as [CODE] (for example [SHOO0010407]); book passages as (Book: <pub>, p. <page>); outside records as (Source: <source>, <title>).
If the dossier is thin or the question is ambiguous, say so plainly. Structure:
## Summary (3 to 5 sentences)
## What the archive holds (counts, date range, categories)
## How it evolved (by decade: shapes, materials, constructions, colours, signature details)
## Signature elements (what makes it recognisably Bally, with object citations)
## Key pieces (6 to 10 objects, one line each on why it matters)
## Outside evidence (books, museums, auctions with prices if present)
## Gaps and open questions (what is missing or uncertain, what to photograph or check)
## Directions for design (3 to 5 concrete, archive-grounded starting points, each citing objects)`;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  if (!GEMINI) return json({ error: "GEMINI_API_KEY is not set" }, 500);
  const auth = req.headers.get("Authorization") ?? "";
  const db = createClient(SB_URL, ANON, { auth: { persistSession: false }, global: { headers: { Authorization: auth, "x-bally-surface": "studio" } } });
  const { data: u } = await db.auth.getUser(auth.replace(/^Bearer\s+/i, ""));
  if (!u?.user) return json({ error: "Sign in as archive staff to use the Lab." }, 401);
  const { data: isStaff } = await db.rpc("is_archive_staff");
  if (!isStaff) return json({ error: "Archive staff only." }, 403);
  let body: any; try { body = await req.json(); } catch { return json({ error: "Bad JSON" }, 400); }
  const question = String(body.question ?? "").trim().slice(0, 300);
  if (question.length < 2) return json({ error: "Ask a question or name a line, model or theme." }, 400);
  const projectId = body.project_id ? String(body.project_id) : null;
  const since = new Date(Date.now() - 3600_000).toISOString();
  const { count } = await db.from("lab_research").select("id", { count: "exact", head: true }).eq("created_by", u.user.id).gte("created_at", since);
  if ((count ?? 0) >= 30) return json({ error: "Hourly limit reached (30 briefs). Try again later." }, 429);

  const { data: row, error: insErr } = await db.from("lab_research").insert({ project_id: projectId, question, status: "running" }).select("id").single();
  if (insErr || !row) return json({ error: "Could not start the research", detail: insErr?.message }, 500);
  const now = () => new Date().toISOString();
  const fail = async (msg: string, dossier: unknown = null) => {
    await db.from("lab_research").update({ status: "error", error: msg.slice(0, 900), dossier, updated_at: now() }).eq("id", row.id);
    return json({ id: row.id, status: "error", error: msg, dossier }, 502);
  };

  const { data: dossier, error: dErr } = await db.rpc("lab_dossier", { q: question, p_max: 300 });
  if (dErr || !dossier) return await fail("The archive search failed: " + (dErr?.message ?? "no data"));
  if (!dossier.objects_analysed) {
    const brief = "## Summary\nThe archive has no objects that match this question. Try a model name, a line, a material or a decade.";
    await db.from("lab_research").update({ status: "ok", dossier, brief, updated_at: now() }).eq("id", row.id);
    return json({ id: row.id, status: "ok", dossier, brief });
  }

  const payload = {
    systemInstruction: { parts: [{ text: SYSTEM }] },
    contents: [{ role: "user", parts: [{ text: `QUESTION: ${question}\n\nDOSSIER (JSON):\n${JSON.stringify(dossier)}` }] }],
    generationConfig: { temperature: 0.3, maxOutputTokens: 12000 },
  };
  let brief = "", used = "";
  const errs: string[] = [];
  for (const m of MODELS) {
    const [ver, model] = m.trim().split("/");
    const r = await fetch(`https://generativelanguage.googleapis.com/${ver}/models/${model}:generateContent`, { method: "POST", headers: { "x-goog-api-key": GEMINI, "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { errs.push(`${model}: ${r.status} ${j?.error?.message ?? ""}`.slice(0, 250)); continue; }
    brief = (j.candidates?.[0]?.content?.parts ?? []).filter((p: any) => !p.thought).map((p: any) => p.text ?? "").join("").trim();
    used = model;
    if (brief) break;
    errs.push(`${model}: empty (${j?.candidates?.[0]?.finishReason ?? "?"})`);
  }
  if (!brief) return await fail("Google did not write a brief: " + errs.join(" | "), dossier);
  brief = brief.replace(/—/g, ", ");
  await db.from("lab_research").update({ status: "ok", dossier, brief, model: used, updated_at: now() }).eq("id", row.id);
  if (projectId) await db.from("studio_projects").update({ updated_at: now() }).eq("id", projectId);
  return json({ id: row.id, status: "ok", model: used, brief, dossier });
});
