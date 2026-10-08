-- Server-owned browser sessions. Never store raw cookies or plaintext Auth tokens.
-- The application encrypts the credential bundle with FINLEDGER_ENC_KEY before calling these functions.
-- No table privileges are granted to finledger_app or Supabase API roles.
create table finledger_private.staff_sessions (
  cookie_hash bytea primary key check (octet_length(cookie_hash) = 32),
  user_id uuid not null references public.users(id) on delete cascade,
  credential_ciphertext bytea not null check (octet_length(credential_ciphertext) > 0),
  access_expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  idle_expires_at timestamptz not null,
  absolute_expires_at timestamptz not null,
  version bigint not null default 1 check (version > 0)
);
create index staff_sessions_user_id_idx on finledger_private.staff_sessions(user_id);
create index staff_sessions_absolute_expires_idx on finledger_private.staff_sessions(absolute_expires_at);
alter table finledger_private.staff_sessions enable row level security;

create table finledger_private.staff_session_events (
  id bigint generated always as identity primary key,
  user_id uuid not null references public.users(id) on delete cascade,
  event text not null check (event in ('started', 'revoked')),
  at timestamptz not null default now()
);
alter table finledger_private.staff_session_events enable row level security;

create function finledger_private.staff_session_start(
  p_hash bytea, p_user_id uuid, p_credentials bytea, p_access_expiry timestamptz
) returns void language plpgsql security definer set search_path = pg_catalog as $$
begin
  if octet_length(p_hash) is distinct from 32
     or octet_length(p_credentials) is null or octet_length(p_credentials) = 0
     or p_access_expiry is null or p_access_expiry <= now()
     or p_access_expiry > now() + interval '1 hour' then
    raise check_violation using message = 'invalid session parameters';
  end if;
  insert into finledger_private.staff_sessions
    (cookie_hash, user_id, credential_ciphertext, access_expires_at, idle_expires_at, absolute_expires_at)
  values (p_hash, p_user_id, p_credentials, p_access_expiry,
          now() + interval '30 minutes', now() + interval '8 hours');
  insert into finledger_private.staff_session_events (user_id, event) values (p_user_id, 'started');
end;
$$;

-- UPDATE atomically checks and extends the idle deadline. A returned row is live at this instant.
create function finledger_private.staff_session_touch(p_hash bytea)
returns table (user_id uuid, credential_ciphertext bytea, access_expires_at timestamptz, version bigint)
language plpgsql security definer set search_path = pg_catalog as $$
begin
  return query
    update finledger_private.staff_sessions s
       set idle_expires_at = least(s.absolute_expires_at, now() + interval '30 minutes')
     where s.cookie_hash = p_hash and s.idle_expires_at > now() and s.absolute_expires_at > now()
    returning s.user_id, s.credential_ciphertext, s.access_expires_at, s.version;
end;
$$;

-- Compare-and-swap prevents two simultaneous refreshes from replacing the newer one with a stale token.
create function finledger_private.staff_session_rotate(
  p_hash bytea, p_version bigint, p_credentials bytea, p_access_expiry timestamptz
) returns boolean language plpgsql security definer set search_path = pg_catalog as $$
begin
  if octet_length(p_credentials) is null or octet_length(p_credentials) = 0
     or p_access_expiry is null or p_access_expiry <= now()
     or p_access_expiry > now() + interval '1 hour' then
    return false;
  end if;
  update finledger_private.staff_sessions s
     set credential_ciphertext = p_credentials, access_expires_at = p_access_expiry, version = s.version + 1
   where s.cookie_hash = p_hash and s.version = p_version
     and s.idle_expires_at > now() and s.absolute_expires_at > now();
  return found;
end;
$$;

create function finledger_private.staff_session_revoke(p_hash bytea)
returns void language plpgsql security definer set search_path = pg_catalog as $$
declare v_user_id uuid;
begin
  delete from finledger_private.staff_sessions s where s.cookie_hash = p_hash returning s.user_id into v_user_id;
  if v_user_id is not null then
    insert into finledger_private.staff_session_events (user_id, event) values (v_user_id, 'revoked');
  end if;
end;
$$;

revoke all on table finledger_private.staff_sessions, finledger_private.staff_session_events from public;
revoke all on function finledger_private.staff_session_start(bytea,uuid,bytea,timestamptz),
  finledger_private.staff_session_touch(bytea),
  finledger_private.staff_session_rotate(bytea,bigint,bytea,timestamptz),
  finledger_private.staff_session_revoke(bytea) from public;
grant execute on function finledger_private.staff_session_start(bytea,uuid,bytea,timestamptz),
  finledger_private.staff_session_touch(bytea),
  finledger_private.staff_session_rotate(bytea,bigint,bytea,timestamptz),
  finledger_private.staff_session_revoke(bytea) to finledger_app;
