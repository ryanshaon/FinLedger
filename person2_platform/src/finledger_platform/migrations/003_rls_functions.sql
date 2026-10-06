-- Row Level Security on every tenant table + the few SECURITY DEFINER lookups that must run before a
-- tenant is known (token -> client, slug -> client, bearer -> user, queue claim across clients).

do $$
declare t text;
begin
  foreach t in array array['vendors', 'documents', 'document_assets', 'jobs', 'erp_links', 'mapping_table',
                           'masters_snapshots', 'open_bills', 'rag_chunks', 'outbox'] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy tenant on %I using (client_id = app_client_id()) with check (client_id = app_client_id())', t);
  end loop;
end $$;

alter table clients enable row level security;
create policy tenant on clients
  using (id = app_client_id() or firm_id = app_firm_id())
  with check (firm_id = app_firm_id() or id = app_client_id());

alter table client_members enable row level security;
create policy tenant on client_members
  using (client_id = app_client_id() or client_id in (select id from clients where firm_id = app_firm_id()))
  with check (client_id = app_client_id() or client_id in (select id from clients where firm_id = app_firm_id()));

alter table users enable row level security;
create policy tenant on users using (firm_id = app_firm_id()) with check (firm_id = app_firm_id());

alter table firms enable row level security;
create policy tenant on firms using (id = app_firm_id());

-- ---------- pre-tenant lookups (SECURITY DEFINER, owner bypasses RLS) ----------

create function auth_user(p_token_hash bytea)
returns table (user_id uuid, firm_id uuid, firm_admin boolean)
language sql stable security definer set search_path = public, pg_temp as $$
  select id, firm_id, firm_admin from users where api_token_hash = p_token_hash
$$;

-- Roles the user holds on the client, or NULL when the user has no access at all.
create function user_client_roles(p_user_id uuid, p_client_id uuid) returns text[]
language sql stable security definer set search_path = public, pg_temp as $$
  select nullif(
    array(select m.role from client_members m where m.user_id = p_user_id and m.client_id = p_client_id order by 1)
    || array(select 'firm_admin'::text from users u join clients c on c.firm_id = u.firm_id
             where u.id = p_user_id and c.id = p_client_id and u.firm_admin),
    '{}')
$$;

create function client_by_public_token(p_token text) returns uuid
language sql stable security definer set search_path = public, pg_temp as $$
  select id from clients where public_token = p_token
$$;

create function client_by_inbound_slug(p_slug text) returns uuid
language sql stable security definer set search_path = public, pg_temp as $$
  select id from clients where inbound_slug = lower(p_slug)
$$;

create function client_by_agent_token(p_token_hash bytea) returns uuid
language sql volatile security definer set search_path = public, pg_temp as $$
  update erp_links set last_seen_at = now() where agent_token_hash = p_token_hash returning client_id
$$;

-- ---------- queue ----------

-- Claim one ready job (queued and due, or running with an expired lease = crashed worker).
-- A job whose lease expires after its last allowed attempt goes to 'dead' (poison queue), not back to work.
create function claim_job(p_queue text, p_lease_seconds int default 300) returns setof jobs
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare j jobs;
begin
  loop
    select * into j from jobs
     where queue = p_queue
       and ((state = 'queued' and run_after <= now()) or (state = 'running' and locked_until < now()))
     order by run_after, id
     limit 1
     for update skip locked;
    if not found then
      return;
    end if;
    if j.attempts >= j.max_attempts then
      update jobs set state = 'dead', locked_until = null, updated_at = now(),
                      last_error = coalesce(last_error || ' | ', '') || 'lease expired on final attempt'
       where id = j.id;
      continue;
    end if;
    update jobs set state = 'running', attempts = attempts + 1, updated_at = now(),
                    locked_until = now() + make_interval(secs => p_lease_seconds)
     where id = j.id returning * into j;
    return next j;
    return;
  end loop;
end $$;

-- Settle a running job. ok -> done. Failure -> retry with backoff, or dead when out of attempts / permanent.
create function finish_job(p_job_id bigint, p_ok boolean, p_error text default null,
                           p_permanent boolean default false, p_retry_seconds int default 30)
returns text
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare j jobs;
begin
  select * into j from jobs where id = p_job_id and state = 'running' for update;
  if not found then
    return null;  -- lease was lost (expired and re-claimed); the other worker owns it now
  end if;
  if p_ok then
    update jobs set state = 'done', locked_until = null, last_error = null, updated_at = now() where id = j.id;
    return 'done';
  end if;
  if p_permanent or j.attempts >= j.max_attempts then
    update jobs set state = 'dead', locked_until = null, last_error = p_error, updated_at = now() where id = j.id;
    return 'dead';
  end if;
  update jobs set state = 'queued', locked_until = null, last_error = p_error, updated_at = now(),
                  run_after = now() + make_interval(secs => p_retry_seconds * power(2, j.attempts - 1)::int)
   where id = j.id;
  return 'queued';
end $$;

-- Ops: put a poison job back in line after the cause is fixed.
create function requeue_dead_job(p_job_id bigint) returns boolean
language sql volatile security definer set search_path = public, pg_temp as $$
  update jobs set state = 'queued', attempts = 0, run_after = now(), updated_at = now()
   where id = p_job_id and state = 'dead' returning true
$$;

-- ---------- grants ----------

grant usage on schema public to finledger_app;
grant select, insert, update, delete on all tables in schema public to finledger_app;
revoke insert, update, delete on firms from finledger_app;
grant usage on all sequences in schema public to finledger_app;
revoke execute on all functions in schema public from public;
grant execute on all functions in schema public to finledger_app;
