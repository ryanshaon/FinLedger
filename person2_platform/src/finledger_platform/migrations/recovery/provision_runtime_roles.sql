-- Operator-only: provision disabled logins; the owner sets passwords privately before enabling them.
begin;
create role fl_api nologin nosuperuser nocreatedb nocreaterole inherit nobypassrls noreplication;
create role fl_control nologin nosuperuser nocreatedb nocreaterole inherit nobypassrls noreplication;
create role fl_ingest nologin nosuperuser nocreatedb nocreaterole inherit nobypassrls noreplication;
create role fl_score nologin nosuperuser nocreatedb nocreaterole inherit nobypassrls noreplication;
create role fl_outbox nologin nosuperuser nocreatedb nocreaterole inherit nobypassrls noreplication;
grant finledger_app to fl_api, fl_control, fl_ingest, fl_score, fl_outbox;
grant finledger_worker_ingest to fl_ingest;
grant finledger_worker_score to fl_score;

do $$
declare role_name text;
begin
  foreach role_name in array array['fl_api','fl_control','fl_ingest','fl_score','fl_outbox'] loop
    if exists (select 1 from pg_roles where rolname = role_name
               and (rolcanlogin or rolsuper or rolbypassrls or rolcreaterole or rolcreatedb or rolreplication))
       or not pg_has_role(role_name, 'finledger_app', 'MEMBER')
       or pg_has_role(role_name, (select relowner from pg_class where oid='public.documents'::regclass), 'MEMBER') then
      raise exception 'runtime role preflight failed: %', role_name;
    end if;
  end loop;
end $$;
commit;
