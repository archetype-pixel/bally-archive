// resend-webhook: Resend posts email outcomes (delivered, bounced, complained...) here.
// URL carries a random token (?k=) stored in app_settings.resend_webhook_token; requests without it are rejected.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
let token: string | null = null;

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("POST only", { status: 405 });
  if (!token) {
    const { data } = await db.from("app_settings").select("value").eq("key", "resend_webhook_token").maybeSingle();
    token = data?.value ?? null;
  }
  const k = new URL(req.url).searchParams.get("k");
  if (!token || k !== token) return new Response("forbidden", { status: 403 });
  let body: any;
  try { body = await req.json(); } catch { return new Response("bad json", { status: 400 }); }
  const type = String(body?.type ?? "");
  if (!type.startsWith("email.")) return new Response("ignored", { status: 200 });
  const event = type.slice(6);
  const d = body.data ?? {};
  const to: string[] = Array.isArray(d.to) ? d.to : d.to ? [d.to] : [];
  const reason = d.bounce?.message ?? d.bounce?.subType ?? d.failed?.reason ?? d.reason ?? null;
  const rows = to.map((email) => ({ resend_id: d.email_id ?? null, email: String(email).toLowerCase(), event, subject: d.subject ?? null, reason, occurred_at: body.created_at ?? new Date().toISOString(), raw: body }));
  if (rows.length) {
    const { error } = await db.from("email_events").upsert(rows, { onConflict: "resend_id,event", ignoreDuplicates: true });
    if (error) { console.error(error.message); return new Response("db error", { status: 500 }); }
  }
  return new Response("ok");
});
