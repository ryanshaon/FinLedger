-- Forward-only hardening for queue ownership, tenant integrity, review revisions and ERP posting audit.

alter table documents add constraint documents_client_id_id_key unique (client_id, id);
alter table users add constraint users_firm_id_id_key unique (firm_id, id);

-- Tenant identity must agree with the referenced document on every cross-seat record.
alter table document_assets add constraint document_assets_tenant_document_fk
  foreign key (client_id, document_id) references documents(client_id, id) on delete cascade;
alter table jobs add constraint jobs_tenant_document_fk
  foreign key (client_id, document_id) references documents(client_id, id) on delete cascade;
alter table canonical_invoices add constraint canonical_invoices_tenant_document_fk
  foreign key (client_id, document_id) references documents(client_id, id) on delete cascade;
alter table risk_assessments add constraint risk_assessments_tenant_document_fk
  foreign key (client_id, document_id) references documents(client_id, id) on delete cascade;
alter table voucher_drafts add constraint voucher_drafts_tenant_document_fk
  foreign key (client_id, document_id) references documents(client_id, id) on delete cascade;
alter table review_tasks add constraint review_tasks_tenant_document_fk
  foreign key (client_id, document_id) references documents(client_id, id) on delete cascade;
alter table correction_events add constraint correction_events_tenant_document_fk
  foreign key (client_id, document_id) references documents(client_id, id) on delete cascade;
alter table approvals add constraint approvals_tenant_document_fk
  foreign key (client_id, document_id) references documents(client_id, id) on delete cascade;
alter table workflow_events add constraint workflow_events_tenant_document_fk
  foreign key (client_id, document_id) references documents(client_id, id) on delete cascade;

alter table review_tasks drop constraint review_tasks_document_id_status_key;
alter table review_tasks add column review_cycle uuid not null default gen_random_uuid();
alter table review_tasks add column draft_revision int not null default 1 check (draft_revision > 0);
create unique index review_tasks_one_open on review_tasks(document_id) where status = 'open';
create index review_tasks_revision_history on review_tasks(client_id, document_id, draft_revision, created_at);

alter table approvals add column superseded_at timestamptz;
alter table approvals add column superseded_by_revision int;

alter table jobs drop constraint jobs_state_check;
alter table jobs add constraint jobs_state_check check (state in ('queued','running','done','dead','cancelled'));
alter table jobs add column claim_token uuid;
update jobs set claim_token=gen_random_uuid() where state='running';
alter table jobs add constraint jobs_running_claim_check
  check ((state = 'running') = (claim_token is not null));

create table posting_attempts (
  id               bigint generated always as identity primary key,
  client_id        uuid not null references clients(id) on delete cascade,
  document_id      uuid not null,
  revision         int not null check (revision > 0),
  job_id           bigint not null references jobs(id),
  idempotency_key  text not null,
  request_hash     text not null,
  result           text not null default 'pending' check (result in ('pending','posted','failed')),
  erp_id           text,
  error            text,
  created_at       timestamptz not null default now(),
  completed_at     timestamptz,
  unique (job_id),
  unique (client_id, idempotency_key),
  foreign key (client_id, document_id) references documents(client_id, id) on delete cascade
);
alter table posting_attempts enable row level security;
create policy tenant on posting_attempts using (client_id = app_client_id()) with check (client_id = app_client_id());
grant select, insert, update on posting_attempts to finledger_app;
grant usage on sequence posting_attempts_id_seq to finledger_app;

do $$
declare q text;
begin
  foreach q in array array['ingest','extract','score','map','post'] loop
    if not exists (select 1 from pg_roles where rolname = 'finledger_worker_' || q) then
      execute format('create role %I nologin', 'finledger_worker_' || q);
    end if;
  end loop;
  if not exists (select 1 from pg_roles where rolname = 'finledger_queue_ops') then
    create role finledger_queue_ops nologin;
  end if;
end $$;

drop function finish_job(bigint, boolean, text, boolean, int);

create or replace function claim_job(p_queue text, p_lease_seconds int default 300) returns setof jobs
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare j jobs; required_role text := 'finledger_worker_' || p_queue;
begin
  if p_queue not in ('ingest','extract','score','map','post') then raise exception 'unknown queue'; end if;
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

create function finish_job(p_job_id bigint, p_claim_token uuid, p_ok boolean, p_error text default null,
                           p_permanent boolean default false, p_retry_seconds int default 30) returns text
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare j jobs; required_role text;
begin
  select * into j from jobs where id=p_job_id and state='running' and claim_token=p_claim_token for update;
  if not found then return null; end if;
  required_role := 'finledger_worker_' || j.queue;
  if not pg_has_role(session_user, required_role, 'MEMBER') then raise exception 'worker role required' using errcode='42501'; end if;
  if p_ok then
    update jobs set state='done',claim_token=null,locked_until=null,last_error=null,updated_at=now() where id=j.id;
    return 'done';
  end if;
  if p_permanent or j.attempts>=j.max_attempts then
    update jobs set state='dead',claim_token=null,locked_until=null,last_error=p_error,updated_at=now() where id=j.id;
    return 'dead';
  end if;
  update jobs set state='queued',claim_token=null,locked_until=null,last_error=p_error,updated_at=now(),
    run_after=now()+make_interval(secs=>p_retry_seconds*power(2,j.attempts-1)::int) where id=j.id;
  return 'queued';
end $$;

create or replace function requeue_dead_job(p_job_id bigint) returns boolean
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare changed int;
begin
  if not pg_has_role(session_user,'finledger_queue_ops','MEMBER') then raise exception 'queue ops role required' using errcode='42501'; end if;
  update jobs set state='queued',attempts=0,claim_token=null,run_after=now(),updated_at=now()
    where id=p_job_id and state='dead';
  get diagnostics changed = row_count;
  return changed = 1;
end $$;

revoke execute on function claim_job(text,int) from finledger_app;
revoke execute on function finish_job(bigint,uuid,boolean,text,boolean,int) from public,finledger_app;
revoke execute on function requeue_dead_job(bigint) from finledger_app;
grant execute on function claim_job(text,int) to finledger_worker_ingest,finledger_worker_extract,finledger_worker_score,finledger_worker_map,finledger_worker_post;
grant execute on function finish_job(bigint,uuid,boolean,text,boolean,int) to finledger_worker_ingest,finledger_worker_extract,finledger_worker_score,finledger_worker_map,finledger_worker_post;
grant execute on function requeue_dead_job(bigint) to finledger_queue_ops;
