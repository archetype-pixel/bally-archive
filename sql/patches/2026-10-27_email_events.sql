-- 2026-10-27: Resend delivery outcomes for archive emails (invites, resets).
-- Token value for the webhook URL lives only in app_settings (not in this repo).
create table if not exists public.app_settings (key text primary key, value text not null);
alter table public.app_settings enable row level security;
revoke all on public.app_settings from anon, authenticated;
create table if not exists public.email_events (
  id bigint generated always as identity primary key, resend_id text, email text not null, event text not null,
  subject text, reason text, occurred_at timestamptz not null default now(), raw jsonb);
create index if not exists email_events_email_idx on public.email_events (lower(email), occurred_at desc);
create unique index if not exists email_events_dedupe on public.email_events (resend_id, event);
alter table public.email_events enable row level security;
revoke all on public.email_events from anon;
create policy "admins read email events" on public.email_events for select to authenticated using (public.is_archive_admin());
create or replace view public.email_status with (security_invoker = true) as
select distinct on (lower(email)) lower(email) as email, event, reason, subject, occurred_at
from public.email_events where event in ('delivered','bounced','complained','delivery_delayed','suppressed','failed')
order by lower(email), occurred_at desc;
grant select on public.email_status to authenticated;
