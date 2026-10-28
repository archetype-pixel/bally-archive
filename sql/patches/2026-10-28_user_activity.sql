-- Per-person activity: who viewed, searched and exported what, and a permanent sign-in history.
-- Admin-only to read. Activity before this patch has no user attached and stays anonymous.

alter table public.events add column if not exists user_id uuid;
create index if not exists events_user_created_idx on public.events (user_id, created_at desc);

create or replace function public.record_archive_event(p_event jsonb)
 returns void language plpgsql security definer set search_path to 'public'
as $function$ declare q text:=left(trim(p_event->>'query'),200); begin
 if p_event->>'kind' not in ('search','object_view','category_view','export','download') then return; end if;
 if q ~ '(@|https?:|[0-9]{7,})' then return; end if;
 insert into public.events(kind,query,object_code,category,result_count,format,user_id)
 values(p_event->>'kind',nullif(q,''),left(p_event->>'object_code',80),left(p_event->>'category',120),
  case when p_event->>'result_count' ~ '^[0-9]{1,9}$' then (p_event->>'result_count')::int else null end,
  left(p_event->>'format',20), auth.uid());
end $function$;

-- Every sign-in creates a new session; record each one permanently.
create table if not exists public.sign_ins (
  id bigserial primary key,
  session_id uuid unique,
  user_id uuid not null,
  email text,
  signed_in_at timestamptz not null default now(),
  user_agent text,
  ip text
);
create index if not exists sign_ins_user_idx on public.sign_ins (user_id, signed_in_at desc);
alter table public.sign_ins enable row level security;
drop policy if exists "admins read sign_ins" on public.sign_ins;
create policy "admins read sign_ins" on public.sign_ins for select to authenticated using ((select public.is_archive_admin()));

create or replace function public.zz_record_sign_in() returns trigger language plpgsql security definer set search_path to 'public'
as $$ begin
  insert into public.sign_ins(session_id,user_id,email,signed_in_at,user_agent,ip)
  values (new.id, new.user_id, (select email from auth.users where id=new.user_id), coalesce(new.created_at, now()), left(new.user_agent,300), host(new.ip))
  on conflict (session_id) do nothing;
  return new;
exception when others then return new; -- never block a sign-in
end $$;
drop trigger if exists zz_record_sign_in on auth.sessions;
create trigger zz_record_sign_in after insert on auth.sessions for each row execute function public.zz_record_sign_in();

-- Backfill from sessions still on file, plus each account's last sign-in.
insert into public.sign_ins(session_id,user_id,email,signed_in_at,user_agent,ip)
select s.id, s.user_id, u.email, s.created_at, left(s.user_agent,300), host(s.ip) from auth.sessions s join auth.users u on u.id=s.user_id
on conflict (session_id) do nothing;
insert into public.sign_ins(user_id,email,signed_in_at)
select u.id, u.email, u.last_sign_in_at from auth.users u
where u.last_sign_in_at is not null and not exists (select 1 from public.sign_ins x where x.user_id=u.id and abs(extract(epoch from x.signed_in_at-u.last_sign_in_at))<120);

-- One row per person for the admin list.
create or replace function public.user_activity_summary()
returns table(user_id uuid, email text, display_name text, role text, sign_ins bigint, first_sign_in timestamptz, last_sign_in timestamptz,
  object_views bigint, category_views bigint, searches bigint, exports bigint, last_active timestamptz)
language plpgsql stable security definer set search_path to 'public'
as $$ begin
  if not public.is_archive_admin() then raise exception 'Admins only'; end if;
  return query
  select u.id, u.email::text, p.display_name, r.role,
    (select count(*) from public.sign_ins s where s.user_id=u.id),
    (select min(s.signed_in_at) from public.sign_ins s where s.user_id=u.id),
    greatest(u.last_sign_in_at,(select max(s.signed_in_at) from public.sign_ins s where s.user_id=u.id)),
    count(e.*) filter (where e.kind='object_view'),
    count(e.*) filter (where e.kind='category_view'),
    count(e.*) filter (where e.kind='search'),
    count(e.*) filter (where e.kind in ('export','download')),
    max(e.created_at)
  from auth.users u
  left join public.archive_profiles p on p.user_id=u.id
  left join public.staff_roles r on r.user_id=u.id
  left join public.events e on e.user_id=u.id
  group by u.id, u.email, p.display_name, r.role, u.last_sign_in_at
  order by greatest(u.last_sign_in_at, max(e.created_at)) desc nulls last;
end $$;

-- Timeline for one person: sign-ins and activity, newest first.
create or replace function public.user_activity(p_user uuid, p_limit int default 200)
returns table(at timestamptz, kind text, detail text, object_code text, category text, result_count int)
language plpgsql stable security definer set search_path to 'public'
as $$ begin
  if not public.is_archive_admin() then raise exception 'Admins only'; end if;
  return query
  select * from (
    select s.signed_in_at, 'sign_in'::text, coalesce(s.user_agent,'')::text, null::text, null::text, null::int from public.sign_ins s where s.user_id=p_user
    union all
    select e.created_at, e.kind, coalesce(e.query, e.format)::text, e.object_code, e.category, e.result_count from public.events e where e.user_id=p_user
  ) t order by 1 desc limit least(greatest(p_limit,1),1000);
end $$;

revoke all on function public.user_activity_summary() from public, anon;
revoke all on function public.user_activity(uuid,int) from public, anon;
grant execute on function public.user_activity_summary() to authenticated;
grant execute on function public.user_activity(uuid,int) to authenticated;
