-- Applied live 2026-10-25 on top of sql/search_objects.sql (plus earlier live edits).
-- 1) Exact model-name bonus (model_name = query +40, word match +30) so "scribe" puts Scribe-named models first.
-- 2) Category intent also lowers the matched count: a bag query does not count shoes as best matches, and a
--    shoe query does not count bags, so they fall into "Related" ("red ladies bag": 442 -> 159 best matches).
do $$
declare d text := pg_get_functiondef('public.search_objects(text,vector,integer)'::regprocedure); n0 text;
begin
  n0 := d;
  d := replace(d, 'select s.guid, s.doc, s.name_text, s.has_image, s.category', 'select s.guid, s.doc, s.name_text, s.has_image, s.category, search_norm(coalesce(s.model_name, '''')) model_n');
  d := replace(d, 'select b.guid, b.has_image, b.name_text, b.category,', 'select b.guid, b.has_image, b.name_text, b.category, b.model_n,');
  d := replace(d, '      coalesce(t.matched, 0) matched,', '      greatest(0, coalesce(t.matched, 0) - case when t.guid is not null and ((q_bag and not q_shoe and t.category = ''SHOES'') or (q_shoe and not q_bag and t.category = ''ACCESSORIES'')) then 1 else 0 end) matched,');
  d := replace(d, '      + case when t.name_text = nq then 25', '      + case when nq = '''' or t.model_n is null or t.model_n = '''' then 0 when t.model_n = nq then 40 when t.model_n ~ (''(^| )'' || nq || ''( |$)'') then 30 else 0 end
      + case when t.name_text = nq then 25');
  if d = n0 then raise exception 'replace failed'; end if;
  execute d;
end $$;
