-- Phase 1 posting: the company a client may post to, a platform switch for it, uncertain outcomes, and post jobs
-- that are claimed per client and never offered again after a lease expires.

alter table erp_links
  add column company_guid text
    check (company_guid ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
  add column posting_enabled_at timestamptz,
  add column posting_enabled_by uuid references users(id),
  add column posting_switch_history jsonb not null default '[]',  -- append-only audit of enable, disable and re-pair
  add constraint erp_links_posting_switch_check check (
    (posting_enabled_at is null and posting_enabled_by is null)
    or (posting_enabled_at is not null and posting_enabled_by is not null and company_guid is not null));

alter table posting_attempts drop constraint posting_attempts_result_check;
alter table posting_attempts
  add constraint posting_attempts_result_check
    check (result in ('pending','posted','failed','outcome_uncertain')),
  add column request_sha256 text check (request_sha256 ~ '^[0-9a-f]{64}$'),
  add column response_sha256 text check (response_sha256 ~ '^[0-9a-f]{64}$'),
  add column reconcile_decision text check (reconcile_decision in ('posted','absent')),
  add column reconciled_by uuid references users(id),
  add column reconciled_at timestamptz,
  add column reconcile_note text;

alter table jobs drop constraint jobs_state_check;
alter table jobs add constraint jobs_state_check
  check (state in ('queued','running','done','dead','cancelled','needs_reconciliation'));
-- A job waiting for a person keeps its claim token, so the agent's late report can still be matched to it.
alter table jobs drop constraint jobs_running_claim_check;
alter table jobs add constraint jobs_running_claim_check
  check ((state in ('running','needs_reconciliation')) = (claim_token is not null));

-- The shared claim never hands out a post job: it has no client filter and would re-offer an expired lease.
create or replace function claim_job(p_queue text, p_lease_seconds int default 300) returns setof jobs
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare j jobs; required_role text := 'finledger_worker_' || p_queue;
begin
  if p_queue not in ('ingest','extract','score','map') then
    if p_queue = 'post' then raise exception 'post jobs use claim_post_job'; end if;
    raise exception 'unknown queue';
  end if;
  if p_lease_seconds < 5 or p_lease_seconds > 3600 then raise exception 'invalid lease'; end if;
  if not pg_has_role(session_user, required_role, 'MEMBER') then raise exception 'worker role required for queue %', p_queue using errcode='42501'; end if;
  loop
    select * into j from jobs where queue=p_queue and
      ((state='queued' and run_after<=now()) or (state='running' and locked_until<now()))
      order by run_after,id limit 1 for update skip locked;
    if not found then return; end if;
    if j.attempts >= j.max_attempts then
      update jobs set state='dead',claim_token=null,locked_until=null,updated_at=now(),
        last_error=coalesce(last_error || ' | ','') || 'lease expired on final attempt' where id=j.id;
      continue;
    end if;
    update jobs set state='running',attempts=attempts+1,updated_at=now(),claim_token=gen_random_uuid(),
      locked_until=now()+make_interval(secs=>p_lease_seconds) where id=j.id returning * into j;
    return next j; return;
  end loop;
end $$;

-- One client's next queued post job, only while that client's posting switch is on. An expired lease means an Import
-- may have left the agent, so that job waits for a person; the claim token is kept so a late report still settles it.
create function claim_post_job(p_client_id uuid, p_lease_seconds int default 300) returns setof jobs
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare j jobs;
begin
  if p_lease_seconds < 5 or p_lease_seconds > 3600 then raise exception 'invalid lease'; end if;
  if not pg_has_role(session_user, 'finledger_worker_post', 'MEMBER') then
    raise exception 'worker role required for queue post' using errcode='42501';
  end if;
  update jobs set state='needs_reconciliation',locked_until=null,updated_at=now(),
    last_error=coalesce(last_error || ' | ','') || 'lease expired; outcome unknown'
    where client_id=p_client_id and queue='post' and state='running' and locked_until<now();
  if not exists (select 1 from erp_links where client_id=p_client_id
                 and posting_enabled_at is not null and company_guid is not null) then
    return;
  end if;
  select * into j from jobs where client_id=p_client_id and queue='post' and state='queued' and run_after<=now()
    order by run_after,id limit 1 for update skip locked;
  if not found then return; end if;
  update jobs set state='running',attempts=attempts+1,updated_at=now(),claim_token=gen_random_uuid(),
    locked_until=now()+make_interval(secs=>p_lease_seconds) where id=j.id returning * into j;
  return next j;
end $$;

-- Close or park a claimed post job. Accepts a late report on a job whose lease expired, as long as the token matches.
create function settle_post_job(p_job_id bigint, p_client_id uuid, p_claim_token uuid, p_state text) returns text
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare j jobs;
begin
  if not pg_has_role(session_user, 'finledger_worker_post', 'MEMBER') then
    raise exception 'worker role required for queue post' using errcode='42501';
  end if;
  if p_state not in ('done','dead','needs_reconciliation') then raise exception 'invalid state'; end if;
  select * into j from jobs where id=p_job_id and client_id=p_client_id and queue='post'
    and state in ('running','needs_reconciliation') and claim_token=p_claim_token for update;
  if not found then return null; end if;
  update jobs set state=p_state,locked_until=null,updated_at=now(),
    claim_token=case when p_state='needs_reconciliation' then claim_token end where id=j.id;
  return p_state;
end $$;

-- A post job is never requeued: the Import may already be in Tally. Other queues behave as before.
create or replace function requeue_dead_job(p_job_id bigint) returns boolean
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare changed int;
begin
  if not pg_has_role(session_user,'finledger_queue_ops','MEMBER') then raise exception 'queue ops role required' using errcode='42501'; end if;
  update jobs set state='queued',attempts=0,claim_token=null,run_after=now(),updated_at=now()
    where id=p_job_id and state='dead' and queue<>'post';
  get diagnostics changed = row_count;
  return changed = 1;
end $$;

revoke execute on function claim_post_job(uuid,int) from public;
revoke execute on function settle_post_job(bigint,uuid,uuid,text) from public;
grant execute on function claim_post_job(uuid,int) to finledger_worker_post;
grant execute on function settle_post_job(bigint,uuid,uuid,text) to finledger_worker_post;

do $$
declare api_role text;
begin
  foreach api_role in array array['anon','authenticated','service_role'] loop
    if exists (select 1 from pg_roles where rolname = api_role) then
      execute format('revoke all on function claim_post_job(uuid,int) from %I', api_role);
      execute format('revoke all on function settle_post_job(bigint,uuid,uuid,text) from %I', api_role);
    end if;
  end loop;
end $$;

-- An edit must also stop at a post job that is waiting for a person: the Import may already be in Tally.
create or replace function public.lock_post_jobs_for_edit(p_document_id uuid) returns boolean
language plpgsql security definer set search_path = public, pg_temp set lock_timeout = '3s' as $$
declare
  tenant_id uuid := public.app_client_id();
  job_state text;
  active boolean := false;
begin
  if tenant_id is null then
    raise exception 'tenant context required' using errcode = '42501';
  end if;
  -- Lock all post jobs, including dead ones which queue operators can requeue.
  for job_state in
    select state from public.jobs where client_id=tenant_id and document_id=p_document_id
      and queue='post' order by id for update
  loop
    if job_state in ('running','done','needs_reconciliation') then active := true; end if;
  end loop;
  return active;
end $$;

-- The app no longer updates jobs directly (016). These are the only ways a post job changes outside claim and settle.

-- Locks a post job and returns its state only while the caller holds its live claim. A result is recorded in the same
-- transaction, so a reconcile, edit or second report cannot slip in between the check and the write.
create function post_claim_state(p_job_id bigint, p_client_id uuid, p_claim_token uuid) returns text
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare job_state text;
begin
  if not pg_has_role(session_user, 'finledger_worker_post', 'MEMBER') then
    raise exception 'worker role required for queue post' using errcode='42501';
  end if;
  if p_client_id is distinct from public.app_client_id() then
    raise exception 'tenant context required' using errcode='42501';
  end if;
  if p_claim_token is null then return null; end if;
  select state into job_state from jobs where id=p_job_id and client_id=p_client_id and queue='post'
    and state in ('running','needs_reconciliation') and claim_token=p_claim_token for update;
  return job_state;
end $$;

-- Hands back a claimed post job the agent never sent. Only the live claim may do it.
create function release_post_job(p_job_id bigint, p_client_id uuid, p_claim_token uuid, p_reason text) returns text
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare job_state text;
begin
  if not pg_has_role(session_user, 'finledger_worker_post', 'MEMBER') then
    raise exception 'worker role required for queue post' using errcode='42501';
  end if;
  update jobs set state='queued',claim_token=null,locked_until=null,updated_at=now(),
    run_after=now()+interval '60 seconds',last_error=left('released: ' || coalesce(p_reason,''), 500)
    where id=p_job_id and client_id=p_client_id and queue='post' and state='running'
      and locked_until>=now() and claim_token=p_claim_token
    returning state into job_state;
  return job_state;
end $$;

-- A person's reconcile decision closes a post job that is waiting for them. Tenant-checked; nothing else moves.
create function reconcile_post_job(p_job_id bigint, p_state text) returns boolean
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare tenant_id uuid := public.app_client_id(); changed int;
begin
  if tenant_id is null then
    raise exception 'tenant context required' using errcode='42501';
  end if;
  if p_state not in ('done','dead') then raise exception 'invalid state'; end if;
  update jobs set state=p_state,claim_token=null,locked_until=null,updated_at=now()
    where id=p_job_id and client_id=tenant_id and queue='post' and state='needs_reconciliation';
  get diagnostics changed = row_count;
  return changed = 1;
end $$;

revoke all on function post_claim_state(bigint,uuid,uuid), release_post_job(bigint,uuid,uuid,text),
  reconcile_post_job(bigint,text) from public;
grant execute on function post_claim_state(bigint,uuid,uuid), release_post_job(bigint,uuid,uuid,text)
  to finledger_worker_post;
grant execute on function reconcile_post_job(bigint,text) to finledger_app;

do $$
declare api_role text;
begin
  foreach api_role in array array['anon','authenticated','service_role'] loop
    if exists (select 1 from pg_roles where rolname = api_role) then
      execute format('revoke all on function post_claim_state(bigint,uuid,uuid), release_post_job(bigint,uuid,uuid,text), reconcile_post_job(bigint,text) from %I', api_role);
    end if;
  end loop;
end $$;
