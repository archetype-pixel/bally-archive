-- Correction layer for files linked to the wrong record, plus attaching stored files that had no record.
-- The original catalogue (objects, object_assets, inventory_*) is untouched. Backups: function_backups '*@2026-10-28'.

-- 1. Hide a wrongly linked file on one record (the file itself stays, and stays on any other record).
create table if not exists public.object_asset_hidden (
  object_guid text not null, asset_guid text not null, reason text, created_at timestamptz default now(),
  primary key (object_guid, asset_guid));
alter table public.object_asset_hidden enable row level security;
drop policy if exists "staff read hidden assets" on public.object_asset_hidden;
create policy "staff read hidden assets" on public.object_asset_hidden for select to authenticated using (archive_tier() is not null);
grant select on public.object_asset_hidden to authenticated;

-- 2. object_page honours hidden files.
CREATE OR REPLACE FUNCTION public.object_page(p_code text)
 RETURNS json LANGUAGE sql STABLE SET search_path TO 'public'
AS $function$
  with o as (
    select guid, object_code, class, category, subcategory, model_name, logo, brand, season, gender, object_type, date_from, date_to, year_label, description, remarks, author, size, quantity, tags, colors, has_spin, main_asset_guid, product_line, wording, technics, copyright, object_notes, caption_country, caption_location, item_type_physical, item_type_digital, on_loan_quantity, inventory_no_notes, collection
    from public.archive_object_current where object_code = p_code limit 1
  ), inv as (
    select i.guid, i.object_guid, i.object_code, i.category, i.inventory_no, i.external_ref, i.availability, i.on_loan_location
    from public.archive_inventory_public i join o on i.object_guid = o.guid
  ), hid as (
    select upper(h.asset_guid) g from public.object_asset_hidden h join o on h.object_guid = o.guid
  ), oa as (
    select a.object_guid, a.asset_guid, a.sort_order, a.source from public.object_assets a join o on a.object_guid = o.guid
    where upper(a.asset_guid) not in (select g from hid)
  ), pa as (
    select distinct ia.asset_guid from public.inventory_assets ia join inv on ia.inventory_guid = inv.guid
    where upper(ia.asset_guid) not in (select g from hid)
  ), wanted as (
    select asset_guid as guid from oa union select asset_guid from pa union select main_asset_guid from o where main_asset_guid is not null
  ), a as (
    select s.guid, s.asset_id, s.filename, s.is_video from public.assets s join wanted w on s.guid = w.guid
  )
  select case when not exists (select 1 from o) then null else json_build_object(
    'object', (select row_to_json(o) from o),
    'originals', (select row_to_json(x) from (select ob.guid, ob.object_code, ob.class, ob.category, ob.subcategory, ob.model_name, ob.logo, ob.brand, ob.season, ob.gender, ob.date_from, ob.date_to, ob.year_label, ob.description, ob.remarks, ob.author, ob.size, ob.quantity, ob.tags, ob.colors, ob.has_spin, ob.main_asset_guid, ob.product_line, ob.wording, ob.technics, ob.copyright, ob.object_notes, ob.caption_country, ob.caption_location, ob.item_type_physical, ob.item_type_digital, ob.on_loan_quantity, ob.inventory_no_notes, ob.collection from public.objects ob join o on ob.guid = o.guid) x),
    'inventory', coalesce((select json_agg(row_to_json(inv) order by inv.inventory_no) from inv), '[]'::json),
    'spins', coalesce((select json_agg(json_build_object('shot_type', sp.shot_type, 'storage_prefix', sp.storage_prefix, 'frames', sp.frames)) from public.spins sp join o on sp.object_guid = o.guid), '[]'::json),
    'uploads', coalesce((select json_agg(json_build_object('guid', u.guid, 'filename', u.filename, 'kind', u.kind, 'display_path', u.display_path, 'thumb_path', u.thumb_path, 'width', u.width, 'height', u.height, 'original_path', u.original_path, 'zoom_path', (select z.dest from public.zoom_copy z where z.guid = u.guid and z.status = 'copied')) order by u.uploaded_at) from public.media_uploads u join o on u.object_guid = o.guid), '[]'::json),
    'overrides', coalesce((select json_agg(json_build_object('object_guid', r.object_guid, 'field', r.field, 'value', r.value)) from public.object_overrides r join o on r.object_guid = o.guid where r.field not in ('estimated_value', 'item_status')), '[]'::json),
    'object_assets', coalesce((select json_agg(row_to_json(oa)) from oa), '[]'::json),
    'piece_assets', coalesce((select json_agg(pa.asset_guid) from pa), '[]'::json),
    'assets', coalesce((select json_agg(row_to_json(a)) from a), '[]'::json),
    'copies', coalesce((select json_agg(json_build_object('asset_id', f.asset_id, 'dest_display', f.dest_display, 'dest_thumb', f.dest_thumb, 'width', f.width, 'height', f.height, 'azure_size', f.azure_size, 'match_note', f.match_note, 'dest_original', f.dest_original)) from public.file_copy f join a on f.asset_id = a.asset_id where f.status = 'copied'), '[]'::json),
    'members', coalesce((select json_agg(json_build_object('collection_id', m.collection_id, 'object_guid', m.object_guid, 'source', m.source, 'confidence', m.confidence, 'reason', m.reason, 'status', m.status)) from public.selection_members m join o on m.object_guid = o.guid where m.status = 'member'), '[]'::json)
  ) end;
$function$;

-- 3. Anniversary books: files were swapped between the two records (checked by reading both PDFs).
--    HIGO0001 (Hundert Jahre Bally-Schuhe 1851-1951) gets the centenary book; HIGO0002 (Fifty years ... 1851-1901) gets the jubilee book.
insert into public.object_asset_hidden(object_guid, asset_guid, reason) values
  ('39728E80-D4D7-4666-9AA8-42719B15A890','A864AA3A-A292-476C-A87C-C526A6C1E131','Swapped: this is the 1901 Fifty Years book, belongs to HIGO0002'),
  ('AC1D0E1D-6224-44F4-8922-4A059E82D856','E45F5168-799B-4A1F-A585-BFF56B85F7DA','Swapped: this is the 1951 Hundred Years book (English), belongs to HIGO0001')
on conflict do nothing;
insert into public.object_overrides(object_guid, field, value, updated_by) values
  ('39728E80-D4D7-4666-9AA8-42719B15A890','main_asset_guid','"E45F5168-799B-4A1F-A585-BFF56B85F7DA"','b9b5e9b2-2174-4f58-b2ef-0d6a93e766d3'),
  ('AC1D0E1D-6224-44F4-8922-4A059E82D856','main_asset_guid','"A864AA3A-A292-476C-A87C-C526A6C1E131"','b9b5e9b2-2174-4f58-b2ef-0d6a93e766d3')
on conflict (object_guid, field) do update set value = excluded.value, updated_at = now();

-- 4. Applied 2026-10-28 (see session): object_additions HIGO0008-0010 (Zürich 1939 Modetheater scans), CORO1128 (1991 CI manual),
--    FLMO0002 (ski instruction film), each with a media_uploads row pointing at the azure-mirror original and the orphans/ cover;
--    media_uploads on HIGO0011 / HIGO0012 for the two Lausanne (Expo 64) PDFs, plus main_asset_guid overrides to those uploads.
--    orphan_files.attached_to records each placement.
-- 5. catalog_index_json / catalog_core_json / catalog_bundle_json: uploads of any kind count as pictures when display_path is a .jpg
--    (document covers, film stills), via replace of kind='photo' with (kind='photo' or display_path ilike '%.jpg').
