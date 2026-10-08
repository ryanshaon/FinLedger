-- Harden the already-deployed helper functions and keep pgvector out of the
-- Data API's public schema. Function bodies use only pg_catalog built-ins.
alter function public.app_client_id() set search_path = pg_catalog;
alter function public.app_firm_id() set search_path = pg_catalog;

-- pgvector is optional in local test databases. On Supabase it is relocatable;
-- moving the extension retains the existing embedding column's type OID.
do $$
begin
  if exists (
    select 1 from pg_extension e
    join pg_namespace n on n.oid = e.extnamespace
    where e.extname = 'vector' and n.nspname = 'public'
  ) then
    create schema if not exists extensions;
    alter extension vector set schema extensions;
  end if;
end $$;
