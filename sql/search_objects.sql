-- search_objects(q, p_vec, p_limit) -> jsonb
-- Ranked Collection search. Read only: uses object_search (materialized copy), search_synonyms, search_vocab, match_image.
create or replace function public.search_objects(q text, p_vec vector default null, p_limit int default 3000)
returns jsonb language plpgsql stable security definer set search_path = public, extensions as $$
declare
  nq text := btrim(search_norm(q));
  toks text[];
  stop text[] := array['and','or','with','the','a','an','of','in','for','on','by','to','from','und','mit','der','die','das','et','le','la','les','de','du','avec','e','il','con'];
  v_gender text; v_decade int; v_yr int;
  groups jsonb := '[]'::jsonb;
  gq tsquery[] := '{}'; gq0 tsquery[] := '{}'; doc_intent boolean;
  anyq tsquery;
  i int := 1; n int; j int; phrase text;
  syn record; variants text[]; corrected text; v text; tq tsquery; k int; hit boolean;
  sem_g text[] := '{}';
  code_hits jsonb;
  result jsonb;
begin
  if nq = '' and p_vec is null then return jsonb_build_object('total', 0, 'hits', '[]'::jsonb); end if;

  -- object code or code prefix
  if nq ~ '^[a-z]{4}[0-9]{3,}$' then
    select jsonb_agg(jsonb_build_array(guid, 1000, 1) order by object_code) into code_hits
    from (select guid, object_code from object_search where upper(object_code) like upper(nq) || '%' order by object_code limit p_limit) s;
    if code_hits is not null then
      return jsonb_build_object('query', q, 'mode', 'code', 'total', jsonb_array_length(code_hits), 'full_matches', jsonb_array_length(code_hits), 'hits', code_hits, 'interpretation', '{}'::jsonb);
    end if;
  end if;

  toks := array(select t from unnest(string_to_array(nq, ' ')) t where t <> '' and not (t = any(stop)));
  n := coalesce(array_length(toks, 1), 0);

  while i <= n loop
    -- v_decade / year
    if toks[i] ~ '^(1[89]|20)[0-9]0s$' then v_decade := left(toks[i], 4)::int; i := i + 1; continue; end if;
    if toks[i] ~ '^(18|19|20)[0-9]{2}$' then v_yr := toks[i]::int; i := i + 1; continue; end if;
    -- v_gender words
    select value into syn from search_synonyms where term = toks[i] and kind = 'gender';
    if found then v_gender := syn.value; i := i + 1; continue; end if;
    -- longest synonym phrase starting here (3, 2, 1 words)
    hit := false; k := least(3, n - i + 1);
    while k >= 1 and not hit loop
      phrase := array_to_string(toks[i:i + k - 1], ' ');
      select * into syn from search_synonyms where term = phrase and kind = 'synonym';
      hit := found;
      if not hit then k := k - 1; end if;
    end loop;
    if not hit then k := 1; phrase := toks[i]; end if;
    j := k;
    variants := array[phrase];
    corrected := null;
    if hit then
      variants := variants || syn.expand;
      if syn.gender_hint is not null and v_gender is null then v_gender := syn.gender_hint; end if;
    end if;
    -- plural / singular
    if length(phrase) > 4 and phrase ~ 's$' and phrase !~ ' ' then variants := variants || regexp_replace(phrase, 'e?s$', ''); end if;
    -- typo: unknown single word with no synonym -> closest known word
    if not hit and phrase !~ ' ' and length(phrase) >= 4
       and not exists (select 1 from search_vocab where word = phrase or word like phrase || '%') then
      select word into corrected from search_vocab where word % phrase order by similarity(word, phrase) desc, ndoc desc limit 1;
      if corrected is not null then
        variants := array[corrected];
        select * into syn from search_synonyms where term = corrected and kind = 'synonym';
        if found then variants := variants || syn.expand; end if;
      end if;
    end if;
    tq := null;
    foreach v in array variants loop
      v := btrim(search_norm(v));
      continue when v = '';
      if v ~ ' ' then tq := coalesce(tq || phraseto_tsquery('simple', v), phraseto_tsquery('simple', v));
      elsif length(v) >= 5 then tq := coalesce(tq || to_tsquery('simple', v || ':*'), to_tsquery('simple', v || ':*'));
      else tq := coalesce(tq || to_tsquery('simple', v), to_tsquery('simple', v));
      end if;
    end loop;
    if tq is not null then
      gq := gq || tq;
      gq0 := gq0 || coalesce(case when phrase ~ ' ' then phraseto_tsquery('simple', coalesce(corrected, phrase)) else to_tsquery('simple', coalesce(corrected, phrase)) end, tq);
      anyq := coalesce(anyq || tq, tq);
      groups := groups || jsonb_build_object('term', phrase, 'variants', to_jsonb(variants), 'corrected_from', case when corrected is not null then phrase end);
    end if;
    i := i + j;
  end loop;

  n := coalesce(array_length(gq, 1), 0);
  doc_intent := nq ~ '(^| )(poster|posters|plakat|plakate|affiche|affiches|advert|adverts|advertisement|advertisements|advertisment|advertising|ad|ads|inserat|photo|photos|photograph|photography|foto|fotos|logo|logos|film|films|catalogue|catalog|katalog|villemot|brochure|label|labels|store|stores|shop|window|document|documents|letter|drawing|sketch|illustration)( |$)';
  if p_vec is not null then
    select array_agg(g order by d) into sem_g from (select object_guid g, min(distance) d from match_image(p_vec, 300) group by 1) x;
    sem_g := coalesce(sem_g, '{}');
  end if;

  with sem as (select g guid, r from unnest(sem_g) with ordinality as u(g, r)),
  base as (
    select s.guid, s.doc, s.name_text, s.has_image, s.category
    from object_search s
    where (v_gender is null or s.gender = v_gender or (v_gender in ('Women','Men') and s.gender = 'Unisex'))
      and (v_decade is null or (s.date_from >= make_date(v_decade, 1, 1) and s.date_from < make_date(v_decade + 10, 1, 1)))
      and (v_yr is null or (s.date_from >= make_date(v_yr, 1, 1) and s.date_from < make_date(v_yr + 1, 1, 1)))
  ),
  text_hits as (
    select b.guid, b.has_image, b.name_text, b.category,
      (select count(*) from unnest(gq) g where b.doc @@ g) matched,
      (select count(*) from unnest(gq0) g where b.doc @@ g) matched0,
      (select count(*) from unnest(gq) g where to_tsvector('simple', b.name_text) @@ g) named,
      (select coalesce(sum(ts_rank('{0.1,0.25,0.5,1.0}', b.doc, g, 1)), 0) from unnest(gq) g where b.doc @@ g) rk
    from base b
    where (n = 0 and p_vec is null) or (n > 0 and b.doc @@ anyq)
  ),
  scored as (
    select coalesce(t.guid, s.guid) guid,
      coalesce(t.matched, 0) matched,
      (case when n = 0 then (case when p_vec is null then 50 else 0 end) else 100.0 * coalesce(t.matched, 0) / n end)
      + 60 * least(coalesce(t.rk, 0) / greatest(n, 1), 0.5)
      + 12 * least(coalesce(t.named, 0), 2)
      + 6 * coalesce(t.matched0, 0)
      + case when t.name_text = nq then 25 when nq <> '' and t.name_text ~ ('(^| )' || nq || '( |$)') then 10 else 0 end
      + case when t.guid is null then 0 when doc_intent then (case when t.category in ('SHOES','ACCESSORIES','COMPONENTS') then 0 else 8 end)
             else (case when t.category in ('SHOES','ACCESSORIES','COMPONENTS') then 8 else 0 end) end
      + case when s.guid is not null then 40.0 * (1 - (s.r - 1) / 300.0) else 0 end
      + case when coalesce(t.has_image, true) then 1 else -5 end score
    from text_hits t
    full join sem s on s.guid = t.guid
  ),
  ranked as (select * from scored where guid in (select guid from base) order by score desc, guid limit p_limit)
  select jsonb_build_object(
    'query', q, 'mode', case when p_vec is not null then 'blend' else 'text' end,
    'interpretation', jsonb_build_object('gender', v_gender, 'decade', v_decade, 'year', v_yr, 'groups', groups),
    'total', (select count(*) from ranked),
    'full_matches', (select count(*) from ranked where n > 0 and matched = n),
    'hits', coalesce((select jsonb_agg(jsonb_build_array(guid, round(score::numeric, 2), matched) order by score desc, guid) from ranked), '[]'::jsonb))
  into result;
  return result;
end $$;
grant execute on function public.search_objects(text, vector, int) to anon, authenticated;
