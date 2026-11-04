-- Operator proof only: PostgreSQL 16+, execute as owner in a session, not as a migration.
-- Synthetic rows, temporary role and grants roll back; sequence values may advance.
begin;
create role zz_proof_rag nologin;
grant finledger_app to zz_proof_rag;
grant zz_proof_rag to current_user with set true;
create temp table zz_rag_results (name text, passed boolean) on commit drop;
grant insert on zz_rag_results to zz_proof_rag;
insert into firms (id, name) values ('00000000-0000-4000-8000-0000000000f2', 'zz-proof-rag');
insert into clients (id, firm_id, name, public_token, inbound_slug) values
 ('00000000-0000-4000-8000-0000000000a2', '00000000-0000-4000-8000-0000000000f2', 'zz-rag-a', 'zz-rag-token-a', 'zz-rag-a'),
 ('00000000-0000-4000-8000-0000000000b2', '00000000-0000-4000-8000-0000000000f2', 'zz-rag-b', 'zz-rag-token-b', 'zz-rag-b');
insert into rag_chunks (client_id, chunk_type, ref, content) values
 ('00000000-0000-4000-8000-0000000000a2', 'memory', 'zz-rag:shared', 'A-original'),
 ('00000000-0000-4000-8000-0000000000b2', 'memory', 'zz-rag:shared', 'B-original');

do $$
declare
 a constant uuid := '00000000-0000-4000-8000-0000000000a2';
 b constant uuid := '00000000-0000-4000-8000-0000000000b2';
 n bigint; denied boolean;
begin
 set local role zz_proof_rag;
 perform set_config('app.client_id', '', true);
 perform set_config('app.firm_id', '', true);
 insert into zz_rag_results select 'no tenant sees no chunks', count(*) = 0 from rag_chunks;
 perform set_config('app.client_id', a::text, true);
 insert into zz_rag_results select 'A sees only its own chunk', count(*) = 1 and bool_and(client_id = a) from rag_chunks;
 begin
   insert into rag_chunks (client_id, chunk_type, ref, content) values (b, 'memory', 'zz-rag:cross', 'bad');
   denied := false;
 exception when insufficient_privilege then denied := true; end;
 insert into zz_rag_results values ('A cannot insert for B', denied);
 update rag_chunks set content = 'bad' where client_id = b; get diagnostics n = row_count;
 insert into zz_rag_results values ('A cannot update B', n = 0);
 delete from rag_chunks where client_id = b; get diagnostics n = row_count;
 insert into zz_rag_results values ('A cannot delete B', n = 0);
 insert into rag_chunks (client_id, chunk_type, ref, content) values (a, 'memory', 'zz-rag:shared', 'A-updated')
   on conflict (client_id, ref) do update set content = excluded.content;
 get diagnostics n = row_count;
 insert into zz_rag_results values ('A can upsert own shared ref', n = 1);
 begin
   insert into rag_chunks (client_id, chunk_type, ref, content) values (b, 'memory', 'zz-rag:shared', 'bad')
     on conflict (client_id, ref) do update set content = excluded.content;
   denied := false;
 exception when insufficient_privilege then denied := true; end;
 insert into zz_rag_results values ('A cannot upsert B', denied);
 perform set_config('app.client_id', b::text, true);
 insert into zz_rag_results select 'B sees own unchanged chunk', count(*) = 1 and bool_and(client_id = b and content = 'B-original') from rag_chunks;
 reset role;
end $$;
do $$
begin
 if (select count(*) from zz_rag_results) <> 8 or exists (select 1 from zz_rag_results where passed is distinct from true) then
   raise exception 'RAG isolation failed: %', (select jsonb_agg(jsonb_build_object('check', name, 'passed', passed)) from zz_rag_results);
 end if;
end $$;
rollback;
select jsonb_build_object('asserted_checks', 8,
 'synthetic_firm_removed', not exists(select 1 from firms where name = 'zz-proof-rag'),
 'temporary_role_removed', not exists(select 1 from pg_roles where rolname = 'zz_proof_rag')) as rollback_proof;
