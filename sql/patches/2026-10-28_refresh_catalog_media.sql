-- catalog_index (materialized) and its inputs copied_assets / object_lead were a one-time snapshot and never refreshed,
-- so files that got a web picture later (PDF covers, re-rendered TIFFs, film stills) never showed in the catalogue.
-- refresh_catalog_media(): adds new pictured assets, gives picture-less objects their first picture (existing leads untouched),
-- refreshes catalog_index. Called by the catalog-bundle workflow before building. First run 2026-10-28: assets +127, objects +121.
create or replace function public.refresh_catalog_media() returns text
language plpgsql security definer set search_path = public as $$
declare a int; b int;
begin
  insert into copied_assets(guid)
  select distinct upper(f.guid) from file_copy f
  where f.status = 'copied' and f.dest_display is not null
    and not exists (select 1 from copied_assets c where c.guid = upper(f.guid));
  get diagnostics a = row_count;
  insert into object_lead(object_guid, first_copied, image_count)
  select oa.object_guid,
         (array_agg(upper(oa.asset_guid) order by oa.sort_order nulls last, upper(oa.asset_guid)))[1],
         count(distinct upper(oa.asset_guid))
  from object_assets oa join copied_assets c on c.guid = upper(oa.asset_guid)
  where not exists (select 1 from object_lead l where l.object_guid = oa.object_guid)
  group by oa.object_guid;
  get diagnostics b = row_count;
  refresh materialized view public.catalog_index;
  return format('assets +%s, objects +%s', a, b);
end $$;
revoke all on function public.refresh_catalog_media() from public, anon, authenticated;
