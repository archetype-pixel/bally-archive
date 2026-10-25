// studio-generate: Design Studio image generation with Google Gemini.
// Isolation: every database and storage call runs AS THE SIGNED-IN STAFF USER (never the service key) and carries the
// header x-bally-surface: studio, which the database refuses on all Collection tables. Output goes only to the private
// "studio" bucket and the studio_items table.
// POST { project_id, source: { kind: 'archive', object_guid } | { kind: 'studio', item_id }, prompt, aspect? }
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const SB_URL = Deno.env.get("SUPABASE_URL")!;
const ANON = Deno.env.get("SUPABASE_ANON_KEY")!;
const GEMINI = Deno.env.get("GEMINI_API_KEY") ?? "";
const MODELS = (Deno.env.get("GEMINI_IMAGE_MODELS") ??
  "v1/gemini-nano-banana-2.1,v1beta/gemini-3.1-flash-image,v1beta/gemini-2.5-flash-image").split(",");
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-bally-surface",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { ...CORS, "Content-Type": "application/json" } });

const GUIDE = "You are a senior footwear and accessories designer at Bally (Swiss luxury house, founded 1851). The image is the starting point for a new design. " +
  "Produce one photorealistic studio product photograph of the new design on a plain light grey background, soft even lighting, same camera angle as the source. " +
  "Keep the object's silhouette, last, proportions, construction and stitching as in the source unless the brief asks to change them. " +
  "No text, no people, no logos other than any already on the object. Design brief: ";

function b64(buf: ArrayBuffer) {
  const bytes = new Uint8Array(buf); let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  if (!GEMINI) return json({ error: "GEMINI_API_KEY is not set" }, 500);
  const auth = req.headers.get("Authorization") ?? "";
  // user-scoped client: RLS applies, Collection tables refuse anything tagged studio
  const db = createClient(SB_URL, ANON, {
    auth: { persistSession: false },
    global: { headers: { Authorization: auth, "x-bally-surface": "studio" } },
  });
  const { data: u } = await db.auth.getUser(auth.replace(/^Bearer\s+/i, ""));
  if (!u?.user) return json({ error: "Sign in as archive staff to use the Design Studio." }, 401);
  const { data: isStaff } = await db.rpc("is_archive_staff");
  if (!isStaff) return json({ error: "Archive staff only." }, 403);

  let body: any;
  try { body = await req.json(); } catch { return json({ error: "Bad JSON" }, 400); }
  const prompt = String(body.prompt ?? "").trim().slice(0, 1500);
  const projectId = String(body.project_id ?? "");
  if (prompt.length < 3 || !projectId) return json({ error: "project_id and prompt are required" }, 400);
  const { data: proj } = await db.from("studio_projects").select("id").eq("id", projectId).maybeSingle();
  if (!proj) return json({ error: "Project not found" }, 404);

  // rate limit: 60 generations per user per hour
  const since = new Date(Date.now() - 3600_000).toISOString();
  const { count } = await db.from("studio_items").select("id", { count: "exact", head: true }).eq("created_by", u.user.id).gte("created_at", since);
  if ((count ?? 0) >= 60) return json({ error: "Hourly limit reached (60). Try again later." }, 429);

  // source image (read only)
  const src = body.source ?? {};
  let srcBytes: ArrayBuffer | null = null, mime = "image/jpeg", sourceKind = "", sourceGuid: string | null = null, sourcePath = "", parentId: string | null = null;
  if (src.kind === "archive" && src.object_guid) {
    sourceKind = "archive"; sourceGuid = String(src.object_guid).toUpperCase();
    const { data: o } = await db.from("archive_object_current").select("main_asset_guid").eq("guid", sourceGuid).maybeSingle();
    if (!o?.main_asset_guid) return json({ error: "This archive object has no photo to start from." }, 400);
    sourcePath = `display/${o.main_asset_guid}.jpg`;
    const r = await fetch(`${SB_URL}/storage/v1/object/public/web/${sourcePath}`);
    if (!r.ok) return json({ error: "Archive photo not found" }, 404);
    mime = r.headers.get("content-type")?.split(";")[0] || mime; srcBytes = await r.arrayBuffer();
  } else if (src.kind === "studio" && src.item_id) {
    sourceKind = "studio"; parentId = String(src.item_id);
    const { data: it } = await db.from("studio_items").select("image_path, source_object_guid").eq("id", parentId).maybeSingle();
    if (!it?.image_path) return json({ error: "Studio step not found" }, 404);
    sourcePath = it.image_path; sourceGuid = it.source_object_guid;
    const { data: blob, error } = await db.storage.from("studio").download(it.image_path);
    if (error || !blob) return json({ error: "Could not read the studio image" }, 500);
    mime = blob.type || "image/png"; srcBytes = await blob.arrayBuffer();
  } else {
    return json({ error: "source must be an archive object or a studio step" }, 400);
  }

  const aspect = ["1:1", "4:3", "3:4", "16:9"].includes(body.aspect) ? body.aspect : "4:3";
  const payload = {
    contents: [{ role: "user", parts: [{ inline_data: { mime_type: mime, data: b64(srcBytes) } }, { text: GUIDE + prompt }] }],
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
  const row = { id, project_id: projectId, parent_item_id: parentId, source_kind: sourceKind, source_object_guid: sourceGuid, source_path: sourcePath, prompt, model: used || null };
  if (!out) {
    await db.from("studio_items").insert({ ...row, status: "error", error: lastErr });
    return json({ error: "Google did not return an image", detail: lastErr }, 502);
  }
  const ext = out.mime.includes("jpeg") ? "jpg" : "png";
  const path = `${projectId}/${id}.${ext}`;
  const bin = Uint8Array.from(atob(out.data), (c) => c.charCodeAt(0));
  const up = await db.storage.from("studio").upload(path, bin, { contentType: out.mime, upsert: false });
  if (up.error) return json({ error: "Saving the image failed", detail: up.error.message }, 500);
  const ins = await db.from("studio_items").insert({ ...row, image_path: path });
  if (ins.error) return json({ error: "Saving the step failed", detail: ins.error.message }, 500);
  await db.from("studio_projects").update({ updated_at: new Date().toISOString() }).eq("id", projectId);
  const { data: signed } = await db.storage.from("studio").createSignedUrl(path, 3600);
  return json({ id, model: used, image_path: path, url: signed?.signedUrl ?? null });
});
