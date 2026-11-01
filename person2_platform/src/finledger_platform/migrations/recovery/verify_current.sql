-- Replacement database verification; adapted from committed migration_012_staging_checks.sql Part C.
-- Run as migration owner only. All synthetic data and temporary role grants roll back.
do $$
begin
  if current_setting('server_version_num')::int < 160000 then
    raise exception 'Part C needs PostgreSQL 16 or newer (found %); stop and report the version', current_setting('server_version');
  end if;
end $$;

begin;
create role zz_proof_012 nologin;
grant finledger_app, finledger_worker_extract to zz_proof_012;
grant zz_proof_012, finledger_worker_extract to current_user with set true;  -- Supabase owner lacks SET; rolled back
create temp table zz_proof_012_results (n serial, check_name text, passed boolean) on commit drop;
grant insert on zz_proof_012_results to zz_proof_012;
grant usage on sequence zz_proof_012_results_n_seq to zz_proof_012;
insert into firms (id, name) values ('00000000-0000-4000-8000-0000000000f1', 'zz-proof-012');
insert into clients (id, firm_id, name, public_token, inbound_slug) values
  ('00000000-0000-4000-8000-00000000000a', '00000000-0000-4000-8000-0000000000f1', 'zz-012-A', 'zz-012-token-a', 'zz-012-a'),
  ('00000000-0000-4000-8000-00000000000b', '00000000-0000-4000-8000-0000000000f1', 'zz-012-B', 'zz-012-token-b', 'zz-012-b');
insert into documents (id, client_id, channel, object_path, content_hash, mime, size_bytes, document_class) values
  ('00000000-0000-4000-8000-0000000000da', '00000000-0000-4000-8000-00000000000a', 'portal',
   '00000000-0000-4000-8000-00000000000a/raw/x.pdf', repeat('a', 64), 'application/pdf', 10, 'invoice'),
  ('00000000-0000-4000-8000-0000000000db', '00000000-0000-4000-8000-00000000000b', 'portal',
   '00000000-0000-4000-8000-00000000000b/raw/x.pdf', repeat('b', 64), 'application/pdf', 10, 'invoice');
insert into llm_usage_rows (client_id, document_id, reservation_id, purpose, model, tokens_in, tokens_out, latency_ms)
  values ('00000000-0000-4000-8000-00000000000b', '00000000-0000-4000-8000-0000000000db', gen_random_uuid(),
          'extract', 'synthetic', 1, 1, 1);
insert into zz_proof_012_results (check_name, passed)
  select 'new firm gets exactly one automation maker', count(*) = 1
    from users where firm_id = '00000000-0000-4000-8000-0000000000f1' and automation;

do $$
declare
  a constant uuid := '00000000-0000-4000-8000-00000000000a';  b constant uuid := '00000000-0000-4000-8000-00000000000b';
  da constant uuid := '00000000-0000-4000-8000-0000000000da'; db constant uuid := '00000000-0000-4000-8000-0000000000db';
  hold constant uuid := '00000000-0000-4000-8000-0000000000e1';
  r text;
begin
  perform set_config('app.client_id', a::text, true);
  set local role zz_proof_012;
  insert into zz_proof_012_results (check_name, passed)
    select 'client A sees no client B usage', count(*) = 0 from llm_usage_rows;
  insert into zz_proof_012_results (check_name, passed) values ('client A maker resolves', automation_maker_id(a) is not null);
  perform llm_reserve_tokens(a, da, hold, 15, 20);
  insert into zz_proof_012_results (check_name, passed)
    select 'reservation held', count(*) = 1 from llm_token_reservations where state = 'held';

  begin perform llm_reserve_tokens(a, da, gen_random_uuid(), 10, 20); r := 'no error';
  exception when others then r := sqlerrm; end;
  insert into zz_proof_012_results (check_name, passed) values ('cap enforced (ai_budget_exceeded)', r = 'ai_budget_exceeded');

  begin perform llm_reserve_tokens(b, db, gen_random_uuid(), 1, 20); r := 'no error';
  exception when others then r := sqlerrm; end;
  insert into zz_proof_012_results (check_name, passed) values ('reserve for client B denied (tenant_mismatch)', r = 'tenant_mismatch');

  begin perform automation_maker_id(b); r := 'no error';
  exception when others then r := sqlerrm; end;
  insert into zz_proof_012_results (check_name, passed) values ('client B maker lookup denied', r = 'tenant_mismatch');

  begin insert into llm_usage_rows (client_id, document_id, reservation_id, purpose, model, tokens_in, tokens_out, latency_ms)
          values (b, db, gen_random_uuid(), 'extract', 'synthetic', 1, 1, 1); r := 'no error';
  exception when insufficient_privilege then r := 'denied'; end;
  insert into zz_proof_012_results (check_name, passed) values ('usage insert for client B denied by RLS', r = 'denied');

  begin insert into llm_token_reservations (reservation_id, client_id, document_id, estimated_tokens)
          values (gen_random_uuid(), a, da, 1); r := 'no error';
  exception when insufficient_privilege then r := 'denied'; end;
  insert into zz_proof_012_results (check_name, passed) values ('direct reservation insert denied', r = 'denied');

  perform llm_reconcile_tokens(hold);
  insert into zz_proof_012_results (check_name, passed)
    select 'reservation reconciled', count(*) = 1 from llm_token_reservations where state = 'reconciled';

  reset role;
  set local role finledger_app;
  begin perform llm_reserve_tokens(a, da, gen_random_uuid(), 1, 20); r := 'no error';
  exception when insufficient_privilege then r := 'denied'; end;
  reset role;
  insert into zz_proof_012_results (check_name, passed) values ('plain finledger_app cannot reserve', r = 'denied');
end $$;

-- Fail the whole proof if even one check fails; execute_sql returns only the last
-- result, so assertions must run before rollback destroys the temporary results.
do $$
begin
  if (select count(*) from zz_proof_012_results) <> 11
     or exists (select 1 from zz_proof_012_results where passed is distinct from true) then
    raise exception 'replacement database proof failed: %',
      (select jsonb_agg(jsonb_build_object('check',check_name,'passed',passed)) from zz_proof_012_results);
  end if;
end $$;
rollback;
select jsonb_build_object(
  'asserted_checks', 11,
  'synthetic_firms_removed', not exists(select 1 from firms where name='zz-proof-012'),
  'temporary_role_removed', not exists(select 1 from pg_roles where rolname='zz_proof_012'),
  'temporary_table_removed', to_regclass('pg_temp.zz_proof_012_results') is null
) as rollback_proof;
