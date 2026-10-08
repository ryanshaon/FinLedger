-- FinLedger uses direct PostgreSQL connections, not the Supabase Data API.
-- Keep the public schema inaccessible to anon/authenticated/service_role even
-- on Supabase projects with legacy automatic grants. This is forward-only so
-- databases that already applied 005 receive the same hardening.

-- These operational tables have no client_id and must remain server-only.
alter table rate_limits enable row level security;
create policy internal_rate_limits on rate_limits to finledger_app
  using (true) with check (true);
alter table schema_migrations enable row level security;

-- Postgres grants EXECUTE on new functions to PUBLIC unless revoked.
revoke execute on all functions in schema public from public;
-- A schema-scoped default REVOKE cannot override PostgreSQL's global PUBLIC
-- function grant. This must be a global default for the migration owner.
alter default privileges revoke execute on functions from public;

do $$
declare api_role text;
begin
  foreach api_role in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select from pg_roles where rolname = api_role) then
      execute format('revoke all privileges on all tables in schema public from %I', api_role);
      execute format('revoke all privileges on all sequences in schema public from %I', api_role);
      execute format('revoke all privileges on all functions in schema public from %I', api_role);
      execute format('alter default privileges in schema public revoke all on tables from %I', api_role);
      execute format('alter default privileges in schema public revoke all on sequences from %I', api_role);
      execute format('alter default privileges in schema public revoke all on functions from %I', api_role);
    end if;
  end loop;
end $$;
