-- PENDING (approval timed out 2026-10-27). ai_object_profile is a materialized view that nothing refreshed,
-- so new or redone AI descriptions never reached the site. Rebuild it with the English fields and add a refresher
-- (called by the enrich job). Full view text: see 2026-10-27_ingest_pipeline.sql section 1, as a materialized view
-- with unique index on object_guid, select granted to authenticated and service_role only.
create or replace function public.refresh_ai_profile()
returns void language sql security definer set search_path = public as $$
  refresh materialized view concurrently public.ai_object_profile;
$$;
