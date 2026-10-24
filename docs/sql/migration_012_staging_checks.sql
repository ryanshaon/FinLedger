-- Migration 012 staging checks. NOT a migration: read-only queries plus one rollback-only proof.
-- Procedure and expected results: docs/DEPLOYMENT_RUNBOOK.md, "Migration 012 staging rollout".
-- Run each part as the owner role in an approved session. Synthetic ids only; record outputs, never DSNs.

-- ---------- Part A: before applying 012 (read-only) ----------

select version(), current_setting('server_version_num')::int >= 160000 as pg16_or_newer;  -- Part C needs true
select string_agg(name, ',' order by name) as applied from public.schema_migrations;
select exists (select from pg_tables where schemaname = 'public' and tablename = 'llm_usage_rows') as has_012_tables,
       exists (select from information_schema.columns
                where table_schema = 'public' and table_name = 'users' and column_name = 'automation') as has_automation_col;
select count(*) as rag_rows, count(*) filter (where ref = '') as empty_refs,
       (select count(*) from (select 1 from rag_chunks group by client_id, ref having count(*) > 1) d) as dup_groups
  from rag_chunks;
select (select count(*) from firms) as firms,
       (select count(*) from users where email like 'automation+%@automation.finledger.invalid') as maker_email_clashes;
select r.rolname, exists (select from pg_roles p where p.rolname = r.rolname) as present
  from (values ('finledger_app'), ('finledger_worker_extract'), ('finledger_worker_score'),
               ('finledger_worker_map'), ('finledger_queue_ops')) r(rolname);

-- ---------- Part B: after applying 012 (read-only) ----------

select name, applied_at from public.schema_migrations
 where name in ('012_ai_durable_state.sql', '013_llm_orphaned_holds.sql', '014_llm_unpriced_usage.sql') order by name;
select relname, relrowsecurity as rls from pg_class
 where relname in ('llm_usage_rows', 'llm_token_reservations') and relnamespace = 'public'::regnamespace order by 1;
select table_name, grantee, string_agg(privilege_type, ',' order by privilege_type) as privs
  from information_schema.role_table_grants
 where table_schema = 'public' and table_name in ('llm_usage_rows', 'llm_token_reservations')
   and grantee <> (select tableowner from pg_tables where tablename = 'llm_usage_rows')
 group by 1, 2 order by 1, 2;
select f.sig, r.rolname from (values ('llm_reserve_tokens(uuid,uuid,uuid,int,int)'), ('llm_reconcile_tokens(uuid)'),
                                     ('llm_release_tokens(uuid)'), ('automation_maker_id(uuid)'),
                                     ('llm_expire_orphaned_tokens(uuid,uuid,bigint,uuid)')) f(sig)  -- 013
  join pg_roles r on r.rolname in ('anon', 'authenticated', 'service_role', 'finledger_app',
                                   'finledger_worker_extract', 'finledger_worker_score', 'finledger_worker_map')
 where has_function_privilege(r.rolname, f.sig, 'execute') order by 1, 2;
select is_nullable, column_default from information_schema.columns  -- 014: expect YES and NULL
 where table_schema = 'public' and table_name = 'llm_usage_rows' and column_name = 'cost_usd';
select (select count(*) from firms) as firms, (select count(*) from users where automation) as automation_makers,
       (select count(*) from rag_chunks where ref = '') as empty_refs_left;

-- ---------- Part C: rollback-only tenant proof (after 012) ----------
-- Creates a transaction-local role and synthetic tenants, records pass/fail, then rolls everything back.
-- Needs PostgreSQL 16+ (GRANT ... WITH SET); on older servers it stops here before writing anything.

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

select check_name, passed from zz_proof_012_results order by n;
rollback;
select count(*) = 0 as nothing_persisted from firms where name = 'zz-proof-012';
