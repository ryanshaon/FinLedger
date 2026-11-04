-- Operator-only staging setup. Set a password privately before enabling login.
-- Do not run via the application migrator; this is not a schema migration.
begin;
create role fl_map nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls noreplication;
-- Worker membership must be inherited: queue functions inspect session_user.
alter role fl_map inherit;
grant finledger_app, finledger_worker_map to fl_map;
do $$
begin
 if exists (select 1 from pg_roles where rolname = 'fl_map' and (rolcanlogin or rolsuper or rolbypassrls or rolcreaterole or rolcreatedb))
    or not pg_has_role('fl_map', 'finledger_app', 'MEMBER')
    or not pg_has_role('fl_map', 'finledger_worker_map', 'MEMBER')
    or pg_has_role('fl_map', (select relowner from pg_class where oid='public.documents'::regclass), 'MEMBER') then
   raise exception 'fl_map role preflight failed';
 end if;
end $$;
commit;
