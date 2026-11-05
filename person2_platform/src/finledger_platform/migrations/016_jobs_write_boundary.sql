-- Worker leases must change through the queue functions, not broad app-table grants.
revoke insert, update, delete on public.jobs from finledger_app;
-- Keep producer-set columns in sync with queue.enqueue and the control service.
grant insert (client_id, document_id, queue, idem_key, payload, max_attempts) on public.jobs to finledger_app;

create function public.lock_post_jobs_for_edit(p_document_id uuid) returns boolean
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
    if job_state in ('running','done') then active := true; end if;
  end loop;
  return active;
end $$;

create function public.cancel_unfinished_post_jobs(p_document_id uuid) returns integer
language plpgsql security definer set search_path = public, pg_temp as $$
declare tenant_id uuid := public.app_client_id(); affected integer;
begin
  if tenant_id is null then
    raise exception 'tenant context required' using errcode = '42501';
  end if;
  update public.jobs set state='cancelled', updated_at=now()
    where client_id=tenant_id and document_id=p_document_id and queue='post' and state in ('queued','dead');
  get diagnostics affected = row_count;
  return affected;
end $$;

revoke all on function public.lock_post_jobs_for_edit(uuid), public.cancel_unfinished_post_jobs(uuid) from public;
do $$
declare api_role text;
begin
  foreach api_role in array array['anon','authenticated','service_role'] loop
    if exists (select 1 from pg_roles where rolname=api_role) then
      execute format('revoke all on function public.lock_post_jobs_for_edit(uuid), public.cancel_unfinished_post_jobs(uuid) from %I', api_role);
    end if;
  end loop;
end $$;
grant execute on function public.lock_post_jobs_for_edit(uuid), public.cancel_unfinished_post_jobs(uuid) to finledger_app;
