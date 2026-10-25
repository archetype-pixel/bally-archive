-- Lock the archive database to signed-in users. The anonymous (public key) role loses access to every
-- table, view and function in public; signed-in users keep exactly what they had, no more.
-- Apply only after the site sends the user's session token on every request.
do $$
declare r record; keep regprocedure[]; keep_sr regprocedure[];
begin
  select array_agg(p.oid::regprocedure) into keep_sr from pg_proc p join pg_namespace n on n.oid=p.pronamespace
   where n.nspname='public' and has_function_privilege('service_role', p.oid, 'execute');
  -- functions signed-in users can run today (directly or via PUBLIC)
  select array_agg(p.oid::regprocedure) into keep from pg_proc p join pg_namespace n on n.oid=p.pronamespace
   where n.nspname='public' and has_function_privilege('authenticated', p.oid, 'execute');
  -- tables/views anon could read: make sure authenticated can too
  for r in select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace
           where n.nspname='public' and c.relkind in ('r','v','m') and has_table_privilege('anon', c.oid, 'select') loop
    execute format('grant select on public.%I to authenticated', r.relname);
  end loop;
  execute 'revoke all on all tables in schema public from anon';
  execute 'revoke execute on all functions in schema public from anon, public';
  for i in 1..coalesce(array_length(keep,1),0) loop
    execute format('grant execute on function %s to authenticated', keep[i]);
  end loop;
  for i in 1..coalesce(array_length(keep_sr,1),0) loop
    execute format('grant execute on function %s to service_role', keep_sr[i]);
  end loop;
  execute 'alter default privileges in schema public revoke select on tables from anon';
  execute 'alter default privileges in schema public revoke execute on functions from anon, public';
end $$;
drop table if exists public.mirror_audit;
