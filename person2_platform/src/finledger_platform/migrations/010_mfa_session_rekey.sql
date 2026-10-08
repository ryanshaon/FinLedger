-- On MFA elevation, retire the AAL1 cookie atomically so a stolen pre-MFA cookie cannot inherit AAL2.
alter table finledger_private.staff_session_events
  drop constraint staff_session_events_event_check;
alter table finledger_private.staff_session_events
  add constraint staff_session_events_event_check check (event in ('started', 'revoked', 'mfa_verified'));

create function finledger_private.staff_session_upgrade(
  p_old_hash bytea, p_new_hash bytea, p_version bigint, p_credentials bytea, p_access_expiry timestamptz
) returns boolean language plpgsql security definer set search_path = pg_catalog as $$
declare v_user_id uuid;
begin
  if octet_length(p_old_hash) is distinct from 32 or octet_length(p_new_hash) is distinct from 32
     or p_old_hash = p_new_hash
     or octet_length(p_credentials) is null or octet_length(p_credentials) = 0
     or p_access_expiry is null or p_access_expiry <= now()
     or p_access_expiry > now() + interval '1 hour' then
    return false;
  end if;
  update finledger_private.staff_sessions s
     set cookie_hash = p_new_hash, credential_ciphertext = p_credentials,
         access_expires_at = p_access_expiry, version = s.version + 1
   where s.cookie_hash = p_old_hash and s.version = p_version
     and s.idle_expires_at > now() and s.absolute_expires_at > now()
  returning s.user_id into v_user_id;
  if v_user_id is null then
    return false;
  end if;
  insert into finledger_private.staff_session_events (user_id, event) values (v_user_id, 'mfa_verified');
  return true;
end;
$$;

revoke all on function finledger_private.staff_session_upgrade(bytea,bytea,bigint,bytea,timestamptz) from public;
grant execute on function finledger_private.staff_session_upgrade(bytea,bytea,bigint,bytea,timestamptz)
  to finledger_app;
