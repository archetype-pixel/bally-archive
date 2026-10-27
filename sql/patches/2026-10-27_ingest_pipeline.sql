-- Ingest pipeline (2026-10-27): new photos from the upload page, the studio drop folder and the bulk drive
-- are processed automatically (AI description, image fingerprints, tags, zoom) and unmatched files wait for review.

-- 1) English translations exposed to the site
create or replace view public.ai_object_profile as
 WITH d AS (
         SELECT ai_descriptions.object_guid, ai_descriptions.model, ai_descriptions.description, ai_descriptions.fields AS f, ai_descriptions.created_at
           FROM ai_descriptions WHERE (ai_descriptions.status = 'done'::text)
        ), th AS (
         SELECT x.object_guid, array_agg(DISTINCT x.norm ORDER BY x.norm) AS themes
           FROM ( SELECT d_1.object_guid,
                        CASE t.value
                            WHEN 'after-ski' THEN 'apres-ski' WHEN 'après-ski' THEN 'apres-ski'
                            WHEN 'sports' THEN NULL WHEN 'business' THEN NULL WHEN 'leisure' THEN NULL WHEN 'winter' THEN NULL
                            WHEN 'playing' THEN NULL WHEN 'outdoor' THEN NULL WHEN 'casual' THEN NULL WHEN 'shopping' THEN NULL
                            WHEN 'curling' THEN 'ice-skating' WHEN 'none' THEN NULL ELSE t.value END AS norm
                   FROM d d_1, LATERAL jsonb_array_elements_text(CASE WHEN jsonb_typeof(d_1.f -> 'themes') = 'array' THEN d_1.f -> 'themes' ELSE '[]'::jsonb END) t(value)) x
          WHERE x.norm IS NOT NULL GROUP BY x.object_guid
        )
 SELECT d.object_guid, d.model, d.description,
    (d.f ->> 'picture_type') AS picture_type,
    COALESCE(th.themes, '{}'::text[]) AS themes,
    NULLIF(NULLIF(d.f ->> 'shoe_type', 'not-a-shoe'), '') AS shoe_type,
    NULLIF(NULLIF(d.f ->> 'bag_type', 'not-a-bag'), '') AS bag_type,
    ARRAY(SELECT jsonb_array_elements_text(CASE WHEN jsonb_typeof(d.f -> 'materials') = 'array' THEN d.f -> 'materials' ELSE '[]'::jsonb END)) AS materials,
    ARRAY(SELECT jsonb_array_elements_text(CASE WHEN jsonb_typeof(d.f -> 'colours') = 'array' THEN d.f -> 'colours' ELSE '[]'::jsonb END)) AS colours,
    ARRAY(SELECT jsonb_array_elements_text(CASE WHEN jsonb_typeof(d.f -> 'details') = 'array' THEN d.f -> 'details' ELSE '[]'::jsonb END)) AS details,
    ARRAY(SELECT x.value FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(d.f -> 'branding') = 'array' THEN d.f -> 'branding' ELSE '[]'::jsonb END) x(value) WHERE x.value <> 'none-visible') AS branding,
    NULLIF(d.f ->> 'visible_text', '') AS visible_text,
    NULLIF(d.f ->> 'era_guess', 'unclear') AS era_guess,
    ((d.f ->> 'people'))::boolean AS people,
    d.created_at,
    NULLIF(NULLIF(d.f ->> 'text_language', ''), 'none') AS text_language,
    NULLIF(d.f ->> 'visible_text_en', '') AS visible_text_en,
    NULLIF(d.f ->> 'wording_en', '') AS wording_en
   FROM d LEFT JOIN th USING (object_guid);
grant select on public.ai_object_profile to authenticated;

-- 2) Staff uploads of films and PDFs were blocked by the web bucket's type list
update storage.buckets set allowed_mime_types = array['image/jpeg','image/png','image/webp','application/json','model/gltf-binary','application/gzip',
  'video/mp4','video/quicktime','video/webm','application/pdf'], file_size_limit = 2147483648 where id = 'web';

-- 3) New objects and uploaded photos get AI descriptions too
create or replace function public.vision_batch(p_shard integer, p_shards integer, p_limit integer)
returns jsonb language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(x), '[]'::jsonb) from (
    select jsonb_build_object(
      'guid', o.guid, 'code', o.object_code, 'class', o.class, 'category', o.category, 'subcategory', o.subcategory,
      'model', o.model_name, 'logo', o.logo, 'gender', o.gender, 'season', o.season,
      'date_from', o.date_from, 'date_to', o.date_to, 'tags', o.tags, 'colors', o.colors,
      'description', o.description, 'notes', o.object_notes, 'wording', o.wording,
      'photos', (select jsonb_agg(p.path order by p.ord) from (
          select path, ord from (
            select fc.dest_display path, case when oa.asset_guid = o.main_asset_guid then 0 else 1 end * 1000 + coalesce(oa.sort_order, 999) ord
              from public.object_assets oa join public.assets a on a.guid = oa.asset_guid
              join public.file_copy fc on fc.asset_id = a.asset_id and fc.status = 'copied' and fc.dest_display is not null
             where oa.object_guid = o.guid
            union all
            select u.display_path, 5000 + row_number() over (order by u.uploaded_at) from public.media_uploads u
             where u.object_guid = o.guid and u.kind = 'photo') q
          order by ord limit 3) p)
    ) x
    from public.archive_object_current o
    where not exists (select 1 from public.ai_descriptions d where d.object_guid = o.guid and d.status in ('done','error'))
      and (exists (select 1 from public.object_assets oa where oa.object_guid = o.guid)
           or exists (select 1 from public.media_uploads u where u.object_guid = o.guid and u.kind = 'photo'))
      and (abs(hashtext(o.guid)) % p_shards) = p_shard
    order by o.object_code
    limit p_limit
  ) s;
$$;

-- 4) Queue uploaded photos for fingerprints, tags and zoom
create or replace function public.enrich_enqueue()
returns jsonb language plpgsql security definer set search_path = public as $$
declare n_embed int; n_zoom int; n_describe int;
begin
  insert into public.embed_queue(asset_guid, object_guid, thumb_path, shard, status, v2_status)
  select u.guid, u.object_guid, u.thumb_path, abs(hashtext(u.guid)) % 16, 'pending', 'pending'
    from public.media_uploads u
   where u.kind = 'photo' and u.thumb_path is not null
     and not exists (select 1 from public.embed_queue q where q.asset_guid = u.guid);
  get diagnostics n_embed = row_count;
  insert into public.zoom_copy(guid, dest_original, shard, status, dest, px, updated_at)
  select u.guid, u.original_path, abs(hashtext(u.guid)) % 16, 'pending', 'zoom/uploads/' || u.guid || '.jpg', 3200, now()
    from public.media_uploads u
   where u.kind = 'photo' and u.original_path is not null and coalesce(u.width, 0) >= 1600
     and not exists (select 1 from public.zoom_copy z where z.guid = u.guid);
  get diagnostics n_zoom = row_count;
  select count(*) into n_describe from public.archive_object_current o
   where not exists (select 1 from public.ai_descriptions d where d.object_guid = o.guid and d.status in ('done','error'))
     and (exists (select 1 from public.object_assets oa where oa.object_guid = o.guid)
          or exists (select 1 from public.media_uploads u where u.object_guid = o.guid and u.kind = 'photo'));
  return jsonb_build_object(
    'queued_embed', n_embed, 'queued_zoom', n_zoom,
    'pending_describe', n_describe,
    'pending_embed', (select count(*) from public.embed_queue where status = 'pending'),
    'pending_tags', (select count(*) from public.embed_queue where status = 'done' and v2_status = 'pending'),
    'pending_zoom', (select count(*) from public.zoom_copy where status = 'pending'),
    'pending_intake', (select count(*) from public.intake_files where status = 'new'));
end $$;

-- 5) Intake: every file dropped under archive/intake/ (bulk drive, studio drop folder) is tracked here
create table if not exists public.intake_files (
  path text primary key,                 -- archive bucket key, intake/<batch>/<relative path>
  batch text not null,
  size bigint,
  ext text,
  kind text check (kind in ('photo','video','document','other')),
  status text not null default 'new' check (status in ('new','attached','needs_review','skipped','error')),
  code_found text,                       -- object code read from file name, folder or embedded photo data
  code_source text,                      -- filename | folder | xmp | exif | staff
  object_guid text,
  suggestions jsonb,                     -- visually similar existing objects [{object_guid, code, score}]
  meta jsonb,                            -- embedded photo data (title, description, keywords, date, camera)
  display_path text, thumb_path text,    -- web bucket derivatives
  width int, height int,
  upload_guid text,                      -- media_uploads row once attached
  error text,
  reviewed_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists intake_files_status on public.intake_files(status, batch);
alter table public.intake_files enable row level security;
drop policy if exists "staff read intake" on public.intake_files;
create policy "staff read intake" on public.intake_files for select to authenticated using (public.is_archive_staff());
grant select on public.intake_files to authenticated;

create or replace function public.intake_scan()
returns integer language plpgsql security definer set search_path = public as $$
declare n int;
begin
  insert into public.intake_files(path, batch, size, ext, kind)
  select s.name, split_part(s.name, '/', 2), (s.metadata->>'size')::bigint,
         lower(nullif(substring(s.name from '\.([A-Za-z0-9]+)$'), '')),
         case when s.name ~* '\.(jpe?g|tiff?|png|heic|webp|bmp|psd|cr2|cr3|nef|arw|dng|3fr|fff|iiq|raf|orf)$' then 'photo'
              when s.name ~* '\.(mp4|mov|m4v|avi|mpg|mpeg|mkv|webm|mxf)$' then 'video'
              when s.name ~* '\.(pdf)$' then 'document' else 'other' end
    from storage.objects s
   where s.bucket_id = 'archive' and s.name like 'intake/%/%'
     and s.name !~ '(^|/)\.' and s.name !~* '(thumbs\.db|desktop\.ini)$'
     and not exists (select 1 from public.intake_files f where f.path = s.name);
  get diagnostics n = row_count;
  update public.intake_files set status = 'skipped', error = 'not a photo, film or PDF', updated_at = now() where status = 'new' and kind = 'other';
  return n;
end $$;

create or replace function public.intake_batch(p_shard integer, p_shards integer, p_limit integer)
returns jsonb language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(to_jsonb(f)), '[]'::jsonb) from (
    select path, batch, size, ext, kind, code_found, code_source, object_guid, display_path, thumb_path, width, height
      from public.intake_files
     where status = 'new' and (abs(hashtext(path)) % p_shards) = p_shard
     order by path limit p_limit) f;
$$;

-- object codes that exist, from a list of candidates
create or replace function public.intake_codes(p_codes text[])
returns jsonb language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_object_agg(upper(object_code), guid), '{}'::jsonb)
    from public.archive_object_current where upper(object_code) = any(select upper(c) from unnest(p_codes) c);
$$;

-- nearest existing objects by image fingerprint (same CLIP model as image_embeddings.embedding2)
create or replace function public.intake_similar(p_embedding extensions.vector, p_k integer default 5)
returns jsonb language sql stable security definer set search_path = public, extensions as $$
  select coalesce(jsonb_agg(jsonb_build_object('object_guid', object_guid, 'code', code, 'score', round(score::numeric, 4)) order by score desc), '[]'::jsonb)
  from (
    select e.object_guid, o.object_code code, max(1 - (e.embedding2 <=> p_embedding)) score
      from (select object_guid, embedding2 from public.image_embeddings where embedding2 is not null order by embedding2 <=> p_embedding limit p_k * 4) e
      join public.archive_object_current o on o.guid = e.object_guid
     group by e.object_guid, o.object_code
     order by score desc limit p_k) s;
$$;

-- staff decisions on files that could not be matched automatically
create or replace function public.intake_assign(p_path text, p_object_guid text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_archive_editor() then raise exception 'Editor access required'; end if;
  if not exists (select 1 from public.archive_object_current where guid = p_object_guid) then raise exception 'Unknown object'; end if;
  update public.intake_files set object_guid = p_object_guid, code_source = 'staff', status = 'new', reviewed_by = auth.uid(), error = null, updated_at = now()
   where path = p_path and status in ('needs_review','error');
  if not found then raise exception 'File is not waiting for review'; end if;
end $$;

create or replace function public.intake_skip(p_path text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_archive_editor() then raise exception 'Editor access required'; end if;
  update public.intake_files set status = 'skipped', reviewed_by = auth.uid(), updated_at = now() where path = p_path and status in ('needs_review','error');
end $$;

create or replace function public.intake_summary()
returns jsonb language sql stable security definer set search_path = public as $$
  select case when not public.is_archive_staff() then null else coalesce(jsonb_agg(jsonb_build_object('batch', batch, 'status', status, 'kind', kind, 'files', n, 'gb', round(gb::numeric, 2))), '[]'::jsonb) end
    from (select batch, status, kind, count(*) n, sum(size) / 1e9 gb from public.intake_files group by 1, 2, 3 order by 1, 2, 3) s;
$$;

revoke execute on function public.enrich_enqueue(), public.intake_scan(), public.intake_batch(integer, integer, integer),
  public.intake_codes(text[]), public.intake_similar(extensions.vector, integer), public.vision_batch(integer, integer, integer) from public, anon, authenticated;
grant execute on function public.enrich_enqueue(), public.intake_scan(), public.intake_batch(integer, integer, integer),
  public.intake_codes(text[]), public.intake_similar(extensions.vector, integer), public.vision_batch(integer, integer, integer) to service_role;
revoke execute on function public.intake_assign(text, text), public.intake_skip(text), public.intake_summary() from public, anon;
grant execute on function public.intake_assign(text, text), public.intake_skip(text), public.intake_summary() to authenticated;
