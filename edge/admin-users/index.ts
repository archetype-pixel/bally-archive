// admin-users: archive ADMINs manage staff accounts.
// POST { action: 'list' }
// POST { action: 'invite', email, name, role }      -> emails an invitation; the person sets their own password
// POST { action: 'reset', email }                   -> emails a password reset link
// POST { action: 'set_password', email, password }
// POST { action: 'create', email, password, role, name? }
// POST { action: 'set_role', email, role, active }
// Caller must be signed in with an active 'admin' row in staff_roles. Passwords are never stored or logged here.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const SB_URL = Deno.env.get("SUPABASE_URL")!;
const ANON = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const SITE = "https://cfbally.lovable.app";
const ALLOWED_ORIGINS = [SITE, "https://id-preview--e1d38bf7-6b76-44f0-bf5b-8cafb02ee48d.lovable.app"];
const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type", "Access-Control-Allow-Methods": "POST, OPTIONS" };
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { ...CORS, "Content-Type": "application/json" } });
const ROLES = ["viewer", "editor", "admin"];

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  const auth = req.headers.get("Authorization") ?? "";
  const userDb = createClient(SB_URL, ANON, { auth: { persistSession: false }, global: { headers: { Authorization: auth } } });
  const { data: u } = await userDb.auth.getUser(auth.replace(/^Bearer\s+/i, ""));
  if (!u?.user) return json({ error: "Sign in first." }, 401);
  const admin = createClient(SB_URL, SERVICE, { auth: { persistSession: false } });
  const { data: me } = await admin.from("staff_roles").select("role, active").eq("user_id", u.user.id).maybeSingle();
  if (!me || me.role !== "admin" || !me.active) return json({ error: "Admins only." }, 403);

  let body: any; try { body = await req.json(); } catch { return json({ error: "Bad JSON" }, 400); }
  const action = String(body.action ?? "");
  const email = String(body.email ?? "").trim().toLowerCase();
  const name = String(body.name ?? "").trim().slice(0, 120);
  const origin = req.headers.get("origin") ?? "";
  const site = ALLOWED_ORIGINS.includes(origin) ? origin : SITE;
  const redirectTo = `${site}/reset-password`;
  const findUser = async () => {
    for (let page = 1; page <= 20; page++) {
      const { data, error } = await admin.auth.admin.listUsers({ page, perPage: 200 });
      if (error) throw error;
      const hit = data.users.find((x) => (x.email ?? "").toLowerCase() === email);
      if (hit) return hit;
      if (data.users.length < 200) return null;
    }
    return null;
  };
  const grant = async (id: string, role: string, active = true) => {
    const { error } = await admin.from("staff_roles").upsert({ user_id: id, role, active, updated_at: new Date().toISOString() });
    if (error) throw error;
    if (active) await admin.from("archive_staff").upsert({ user_id: id });
    else await admin.from("archive_staff").delete().eq("user_id", id);
    if (name) await admin.from("archive_profiles").upsert({ user_id: id, display_name: name, updated_at: new Date().toISOString() });
  };

  try {
    if (action === "list") {
      const { data, error } = await admin.auth.admin.listUsers({ page: 1, perPage: 500 });
      if (error) throw error;
      const { data: roles } = await admin.from("staff_roles").select("user_id, role, active");
      const { data: profiles } = await admin.from("archive_profiles").select("user_id, display_name");
      const rm = new Map((roles ?? []).map((r: any) => [r.user_id, r]));
      const pm = new Map((profiles ?? []).map((p: any) => [p.user_id, p.display_name]));
      return json({ users: data.users.map((x) => ({ user_id: x.id, email: x.email, display_name: pm.get(x.id) ?? null, confirmed: !!x.email_confirmed_at, invited_at: x.invited_at ?? null, last_sign_in_at: x.last_sign_in_at, created_at: x.created_at, role: rm.get(x.id)?.role ?? null, active: rm.get(x.id)?.active ?? false })) });
    }
    if (!email.includes("@")) return json({ error: "Enter a valid email." }, 400);

    if (action === "invite") {
      const role = ROLES.includes(body.role) ? body.role : "viewer";
      const existing = await findUser();
      if (existing && existing.last_sign_in_at) return json({ error: "This person already has an account. Change their access or email them a reset link instead." }, 409);
      if (existing) {
        // Invited before but never signed in: send a fresh invitation.
        await admin.from("archive_staff").delete().eq("user_id", existing.id);
        await admin.from("staff_roles").delete().eq("user_id", existing.id);
        await admin.from("archive_profiles").delete().eq("user_id", existing.id);
        const { error: delErr } = await admin.auth.admin.deleteUser(existing.id);
        if (delErr) return json({ error: `Could not refresh the earlier invitation: ${delErr.message}` }, 500);
      }
      const { data, error } = await admin.auth.admin.inviteUserByEmail(email, { data: { display_name: name || null }, redirectTo });
      if (error || !data.user) return json({ error: `The invitation could not be sent: ${error?.message ?? "unknown error"}` }, 502);
      await grant(data.user.id, role);
      return json({ ok: true, email, role });
    }

    if (action === "reset") {
      const target = await findUser();
      if (!target) return json({ error: "No account with that email." }, 404);
      const { error } = await userDb.auth.resetPasswordForEmail(email, { redirectTo });
      if (error) return json({ error: `The reset email could not be sent: ${error.message}` }, 502);
      return json({ ok: true, email });
    }

    if (action === "set_password" || action === "create") {
      const password = String(body.password ?? "");
      if (password.length < 8) return json({ error: "Password must be at least 8 characters." }, 400);
      if (action === "set_password") {
        const target = await findUser();
        if (!target) return json({ error: "No account with that email." }, 404);
        const { error } = await admin.auth.admin.updateUserById(target.id, { password, email_confirm: true });
        if (error) throw error;
        return json({ ok: true, email });
      }
      const role = ROLES.includes(body.role) ? body.role : "viewer";
      if (await findUser()) return json({ error: "An account with that email already exists. Use Set password." }, 409);
      const { data, error } = await admin.auth.admin.createUser({ email, password, email_confirm: true });
      if (error || !data.user) throw error ?? new Error("create failed");
      await grant(data.user.id, role);
      return json({ ok: true, email, role });
    }

    if (action === "set_role") {
      const target = await findUser();
      if (!target) return json({ error: "No account with that email." }, 404);
      if (target.id === u.user.id && (body.role !== "admin" || body.active === false)) return json({ error: "You cannot remove your own admin access." }, 400);
      const role = ROLES.includes(body.role) ? body.role : "viewer";
      const active = body.active !== false;
      if (!(role === "admin" && active)) {
        const { data: admins } = await admin.from("staff_roles").select("user_id").eq("role", "admin").eq("active", true);
        if ((admins ?? []).filter((a: any) => a.user_id !== target.id).length === 0) return json({ error: "The last active administrator cannot be removed." }, 400);
      }
      await grant(target.id, role, active);
      return json({ ok: true, email, role, active });
    }
    return json({ error: "Unknown action" }, 400);
  } catch (e) {
    return json({ error: (e as Error).message ?? "Failed" }, 500);
  }
});
