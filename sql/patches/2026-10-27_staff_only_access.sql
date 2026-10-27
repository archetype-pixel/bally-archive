-- 2026-10-27: archive data readable only by active staff (staff_roles.active), not any signed-in account.
-- Before: 13 SELECT policies were `true` for role authenticated, and is_archive_staff() ignored suspension.
create or replace function public.is_archive_staff() returns boolean language sql stable security definer set search_path to 'public'
as $f$ select public.archive_tier() is not null $f$;
do $$ declare r record; begin
 for r in select tablename, policyname from pg_policies where schemaname='public' and roles::text ilike '%authenticated%' and (qual is null or qual='true') and cmd='SELECT'
   and tablename in ('objects','inventory_items','assets','object_assets','inventory_assets','spins','collections','collection_items','lookups','file_copy','archive_profiles','media_uploads','home_summary') loop
  execute format('alter policy %I on public.%I using (public.archive_tier() is not null)', r.policyname, r.tablename);
 end loop; end $$;
