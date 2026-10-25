// lab-generate: staff-only Google Gemini image variations from an archive photo, for the Lab.
// POST { lab_slug, object_guid?, source_path?, prompt, aspect? }  ->  { id, url, model }
// source_path is a path in the public "web" bucket (default display/<main_asset_guid>.jpg of object_guid).
// Results are saved to web/lab/<slug>/gen/<id>.png and logged in lab_generations. Never touches catalogue tables.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const SB_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const GEMINI = Deno.env.get("GEMINI_API_KEY") ?? "";
const MODELS = (Deno.env.get("GEMINI_IMAGE_MODELS") ??
  "v1/gemini-nano-banana-2.1,v1beta/gemini-3.1-flash-image,v1beta/gemini-2.5-flash-image").split(",");
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { ...CORS, "Content-Type": "application/json" } });

const GUIDE = "You are a footwear designer at Bally (Swiss luxury shoe house, founded 1851) working from a photograph of a shoe in the Bally heritage archive. " +
  "Produce one photorealistic studio product photograph of the new version on a plain light grey background, soft even lighting, same camera angle as the source. " +
  "Keep the shoe's last, silhouette, proportions, construction and stitching exactly as in the source unless the brief asks to change them. " +
  "No text, no logos other than any already on the shoe, no people. Design brief: ";

function b64(buf: ArrayBuffer) {
  const bytes = new Uint8Array(buf); let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  if (!GEMINI) return json({ error: "GEMINI_API_KEY is not set" }, 500);
  const admin = createClient(SB_URL, SERVICE, { auth: { persistSession: false } });

  // staff only: a signed-in archive_staff user, or the service key (server tests)
  const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "");
  let userId: string | null = null;
  if (token !== SERVICE) {
    const { data, error } = await admin.auth.getUser(token);
    if (error || !data?.user) return json({ error: "Sign in as archive staff to generate." }, 401);
    const { data: st } = await admin.from("archive_staff").select("user_id").eq("user_id", data.user.id).maybeSingle();
    if (!st) return json({ error: "Archive staff only." }, 403);
    userId = data.user.id;
  }

  let body: any;
  try { body = await req.json(); } catch { return json({ error: "Bad JSON" }, 400); }
  const slug = String(body.lab_slug ?? "").replace(/[^a-z0-9-]/g, "");
  const prompt = String(body.prompt ?? "").trim().slice(0, 1200);
  if (!slug || prompt.length < 3) return json({ error: "lab_slug and prompt are required" }, 400);

  // rate limit: 60 per lab per hour
  const since = new Date(Date.now() - 3600_000).toISOString();
  const { count } = await admin.from("lab_generations").select("id", { count: "exact", head: true }).eq("lab_slug", slug).gte("created_at", since);
  if ((count ?? 0) >= 60) return json({ error: "Hourly limit reached (60). Try again later." }, 429);

  let src = String(body.source_path ?? "");
  const guid = body.object_guid ? String(body.object_guid).toUpperCase() : null;
  if (!src && guid) {
    const { data: o } = await admin.from("archive_object_current").select("main_asset_guid").eq("guid", guid).maybeSingle();
    if (o?.main_asset_guid) src = `display/${o.main_asset_guid}.jpg`;
  }
  if (!/^(display|thumb|lab)\/[A-Za-z0-9/_.-]+\.(jpg|jpeg|png|webp)$/.test(src)) return json({ error: "No usable source image" }, 400);
  const imgRes = await fetch(`${SB_URL}/storage/v1/object/public/web/${src}`);
  if (!imgRes.ok) return json({ error: `Source image not found (${src})` }, 404);
  const mime = imgRes.headers.get("content-type")?.split(";")[0] || "image/jpeg";
  const srcB64 = b64(await imgRes.arrayBuffer());

  const aspect = ["1:1", "4:3", "3:4", "16:9"].includes(body.aspect) ? body.aspect : "4:3";
  const payload = {
    contents: [{ role: "user", parts: [{ inline_data: { mime_type: mime, data: srcB64 } }, { text: GUIDE + prompt }] }],
    generationConfig: { responseModalities: ["TEXT", "IMAGE"], imageConfig: { aspectRatio: aspect } },
  };
  let out: { data: string; mime: string } | null = null, used = "", lastErr = "";
  for (const m of MODELS) {
    const [ver, model] = m.trim().split("/");
    const r = await fetch(`https://generativelanguage.googleapis.com/${ver}/models/${model}:generateContent`, {
      method: "POST", headers: { "x-goog-api-key": GEMINI, "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { lastErr = `${model}: ${r.status} ${j?.error?.message ?? ""}`.slice(0, 400); if (r.status === 404 || r.status === 400) continue; break; }
    for (const c of j.candidates ?? []) for (const p of c?.content?.parts ?? []) {
      const d = p.inlineData ?? p.inline_data;
      if (d?.data && !out) out = { data: d.data, mime: d.mimeType ?? d.mime_type ?? "image/png" };
    }
    used = model;
    if (out) break;
    lastErr = `${model}: no image returned (${j?.candidates?.[0]?.finishReason ?? j?.promptFeedback?.blockReason ?? "unknown"})`;
  }
  const id = crypto.randomUUID();
  if (!out) {
    await admin.from("lab_generations").insert({ id, lab_slug: slug, source_object_guid: guid, source_image_path: src, prompt, model: used || null, status: "error", error: lastErr, created_by: userId });
    return json({ error: "Google did not return an image", detail: lastErr }, 502);
  }
  const ext = out.mime.includes("jpeg") ? "jpg" : "png";
  const path = `lab/${slug}/gen/${id}.${ext}`;
  const bin = Uint8Array.from(atob(out.data), (c) => c.charCodeAt(0));
  const up = await admin.storage.from("web").upload(path, bin, { contentType: out.mime, upsert: false });
  if (up.error) return json({ error: "Upload failed", detail: up.error.message }, 500);
  await admin.from("lab_generations").insert({ id, lab_slug: slug, source_object_guid: guid, source_image_path: src, prompt, model: used, image_path: path, created_by: userId });
  return json({ id, model: used, path, url: `${SB_URL}/storage/v1/object/public/web/${path}` });
});
