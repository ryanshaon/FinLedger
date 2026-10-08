-- Link an already-verified Supabase Auth subject to a provisioned staff user.
-- Do not auto-link by email or by user-editable JWT metadata: an administrator
-- must explicitly set auth_subject after verifying the identity/invitation.
alter table public.users add column auth_subject uuid unique;

-- Resolve identity before tenant/firm context is known. Keep this privileged
-- lookup in an unexposed schema, with fixed search_path and narrow EXECUTE.
create schema if not exists finledger_private;
revoke all on schema finledger_private from public;
grant usage on schema finledger_private to finledger_app;

create function finledger_private.auth_user_by_subject(p_subject uuid)
returns table (user_id uuid, firm_id uuid, firm_admin boolean)
language sql stable security definer set search_path = pg_catalog as $$
  select u.id, u.firm_id, u.firm_admin
  from public.users u
  where u.auth_subject = p_subject
$$;

revoke all on function finledger_private.auth_user_by_subject(uuid) from public;
grant execute on function finledger_private.auth_user_by_subject(uuid) to finledger_app;
