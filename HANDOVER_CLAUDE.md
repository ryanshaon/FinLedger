# FinLedger handover for Claude — 2026-10-08

## Latest verified update (read this first)

All three subagents completed. The changes remain uncommitted/unpushed at this update. Fresh local checks outside the Windows sandbox: Person 1 **41 passed** plus mock evals (3/3 structural goldens, 5/5 deterministic extraction); Person 2 **74 passed**; Person 3 **38 passed**. Person 2 and 3 each emitted one dependency deprecation warning. The Windows sandbox itself blocked/stalled `initdb`, so use a working test environment or CI for subsequent runs. The new GitHub Actions workflow is not yet verified on GitHub.

The hosted Supabase RLS two-client rollback-only test **passed** under `finledger_app`: no tenant context saw zero clients; client A saw only itself; cross-client update and insert were blocked. A final query verified zero synthetic records and no temporary role membership. This is a database-layer proof, not an end-to-end API/runtime-login proof. Supabase S3 upload/download and real email delivery remain untested because no real service credentials were provisioned.

`person2_platform/README.md` and both `.env.example` files now document 007, S3 endpoint settings, and the outbox worker. Outbox failures remain `failed` for manual handling; there is no managed retry/global scheduler. Temporary `.local-test-tmp/` and `.test-tmp/` directories were removed after checking their scope. The older "at handover" status below describes the initial handover moment and is superseded by this update where they differ.

## Mission and scope

The user asked for all feasible Person 1–3 / production-readiness work **without Person 4**. Continue carefully, verify before claiming completion, and do not edit `person4_tally/`. The intended product is a sellable multi-tenant AP application, not merely a demo. The user specifically requested this handover before the Codex usage limit. This document is a status report, not a substitute for reading the source and tests.

Repository: `C:\Users\ryans\OneDrive\Documents\Studies\Projects\FinTechShi\FinLedger_GitHub`.
Git `origin`: `https://github.com/ryanshaon/FinLedger.git`; `upstream`: `https://github.com/jastiruthvik-web/finledger-ap.git`. At handover, HEAD is `2e8a00d Integrate Persons 1-3 platform and control workflows`. Changes described below are **uncommitted and unpushed**. Preserve any later user/agent edits and inspect `git status` first.

## What exists

| Area | Location | State |
|---|---|---|
| Person 1 AI/RAG prototype | `packages/`, `evals/`, `person1_ai_rag/` | Mock evaluation works; live provider/persistence/integration must be audited. |
| Person 2 API, tenant DB, workers, ingestion, storage | `person2_platform/` | Existing implementation and tests; hardened migrations added this session. |
| Person 3 control/review UI and workflow | `person3_control_ui/` | Existing implementation and tests; browser auth integration is not yet production-ready. |
| Person 4 | `person4_tally/` | Explicitly out of scope. |

Use `.env.example` as the **mock-only** template. The real `.env` is ignored; never print, commit, or paste credentials. The current code uses `FINLEDGER_DATABASE_URL` (non-owner app DB role) and `FINLEDGER_OWNER_DATABASE_URL` (migration owner). Do not run the API as `postgres` / service role / another RLS-bypassing owner.

## Supabase staging: actions already completed

Project ref `hxymklwqifwojziewtcv`, URL `https://hxymklwqifwojziewtcv.supabase.co`, region `ap-south-1`. Treat it as **staging**, with no customer data. Before deployment, its `public` app tables and migration history were empty. Supabase `apply_migration` successfully applied these in order:

1. `finledger_000_migration_history` — created `public.schema_migrations` for the repository's custom migration runner.
2. `finledger_001_tenancy` through `finledger_005_security_workflow` — exact local SQL files `001`–`005`, each followed by the matching `schema_migrations` row.
3. `finledger_006_supabase_data_api_hardening` — local `006_supabase_data_api_hardening.sql` plus history row.
4. `finledger_007_supabase_security_advisors` — local `007_supabase_security_advisors.sql` plus history row.

Hosted verification: 24 public application tables all had RLS enabled. `anon`, `authenticated`, and `service_role` had no direct SELECT privilege on any of the 24. The Supabase security advisor's earlier warnings for mutable helper-function search paths and pgvector in `public` disappeared after 007. One informational lint remains: `schema_migrations` has RLS with no policy. This is intentional (server-only, no API-role access), not evidence of customer access.

Storage bucket `finledger-documents-dev` was created as **private** (`public=false`), 25 MiB per-file limit (`26214400`) matching the API setting. No objects or S3 keys were created; connectivity and object isolation have **not** been tested. The project's S3 credentials must be generated in Supabase Storage > S3 Configuration by the owner and stored only in deployment secret management. Endpoint per Supabase docs: `https://hxymklwqifwojziewtcv.supabase.co/storage/v1/s3`; region `ap-south-1`. Do not use placeholder keys against staging.

Relevant official docs: https://supabase.com/docs/guides/database/database-linter and https://supabase.com/docs/guides/self-hosting/copy-from-platform-s3 (shows bucket SQL and S3 endpoint form).

## Local files changed in this session

- `person2_platform/src/finledger_platform/migrations/006_supabase_data_api_hardening.sql`: revokes Supabase Data API grants and public function execution; enables RLS on `rate_limits` and `schema_migrations`.
- `person2_platform/src/finledger_platform/migrations/007_supabase_security_advisors.sql`: fixed `search_path` for helper functions and moved pgvector to `extensions` when installed.
- `person2_platform/tests/test_supabase_privileges.py`: test of default grants, current grants, RLS, helper search paths, and extension schema.
- `person2_platform/README.md`: earlier documentation update for 006. Check whether it needs 007 and staging notes.
- `.github/workflows/ci.yml`: parallel agent's new CI for Person1 tests/evals, Person2, Person3 on Python 3.12. It is unverified on GitHub Actions.
- `person2_platform/tests/test_storage.py` and perhaps `config.py`, `store.py`: storage agent work in progress at handover; inspect state and tests.
- `person2_platform/src/finledger_platform/outbox_worker.py`, `person2_platform/tests/test_outbox_worker.py` and perhaps CLI: outbox agent work in progress at handover; inspect state and tests.
- `person2_platform/.test-tmp/`: an untracked temporary test directory appeared while the storage/outbox agents were still running. Inspect it before cleanup; do not commit it.
- `evals/latest_report.md`: modified as a side effect of running Person1 evaluations. Check diff before committing; preserve useful report or restore only this known generated diff intentionally.

Three subagents were assigned disjoint work: storage S3 compatibility, outbox worker/Resend adapter, and CI. The CI agent reported completion: Person1 41 tests and two mock eval checks passed locally; both lockfiles passed `uv lock --check`. Its Person2/Person3 jobs were not run locally. Storage/outbox agents had not reported completion when this handover was written. At the last status check, `config.py`, `store.py`, `cli.py`, storage tests and outbox files were already changing. Their files may continue to change; do not overwrite concurrently.

## Test status and environment caveat

Before this session, Person2 had 61 passing tests plus one optional skip, Person3 had 38 passing tests. This is **not** a claim that the new changes pass. A run of the new privilege test via `uv run` failed because the Windows sandbox could not write uv's interpreter cache. A direct `.venv/Scripts/python.exe -m pytest ...` run then failed at `initdb` startup under the Windows sandbox, before test code executed. Diagnose the test environment or rely on CI/Linux; do not misstate this as a code failure or success. Try inspecting the captured `initdb` stderr, setting a safe writable temp directory, or a local PostgreSQL owner DSN. Do not expose credentials in logs.

The new 007 migration was successfully applied to hosted staging and the Supabase security advisor was re-run, but its local test was not completed. Re-run all tests and review coverage before commit/push.

## Important unresolved production work (excluding Person 4)

1. **Credential provisioning**: No real app DB login / secret material / S3 access key pair / Resend key is configured. Use a non-owner, non-BYPASSRLS login role that is a member only of intended `finledger_app`/worker roles. Keep migration-owner credentials out of runtime. Store credentials in a secret manager, not Git.
2. **Tenant-isolation proof on hosted DB**: Run an explicit two-client cross-tenant test using the actual non-owner app login or safe rollback-only synthetic data. Confirm reads and writes fail closed without tenant context and cannot cross client boundaries. Current read-only grant audit is necessary but not sufficient.
3. **Supabase Storage integration**: Validate the agent's endpoint/region/path-style changes, generate S3 credentials securely, test private upload/download and cross-tenant authorization. A private bucket alone is not sufficient; app-signed download authorization and key design matter. No real objects currently exist.
4. **Customer sign-in**: The user answered “whichever is best”; recommend Supabase Auth email login with firm/client membership mapping, MFA for staff/admin, and SSO later. Current Person2 bearer-token / Person3 reverse-proxy token-injection approach is **not** a complete browser authentication system. Do not claim Supabase Auth is integrated. Review API and UI session/CSRF/authorization design before implementation.
5. **Outbound and inbound email**: Verify outbox agent delivery/retry/idempotency and wire a real provider such as Resend only after secrets/domain verification. Inbound domain/webhook, sender authentication and abuse controls still need real integration tests.
6. **Person1 integration**: Audit the actual live provider path, persistence, model/prompt safety, evaluation baselines, PII handling, and failure modes. Mock eval passing does not prove production AI readiness. Keep kill switch available.
7. **Infrastructure**: Supabase hosts Postgres/Storage, **not** these Python API/background services. The user asked for hosting options. Options include Render (simple), Fly.io, and AWS ECS/Fargate. We recommended AWS ECS/Fargate in Mumbai for a sellable production deployment colocated with Supabase `ap-south-1`, but **no paid hosting or AWS resources were provisioned**. See https://docs.aws.amazon.com/AmazonECS/latest/developerguide/AWS_Fargate-Regions.html and https://render.com/docs/regions. Prepare containers, worker topology, health checks, secret management, backups/restore, observability, and staging deployment; ask for account access/approval before incurring paid resources.
8. **Security/performance**: Supabase performance advisor listed 27 unindexed foreign keys, which need workload-informed indexing; 12 “unused indexes” are expected on the empty staging DB, so do not drop them solely on that lint. Security advisor has only the intentional informational RLS/no-policy finding mentioned above. Review schema functions, Data API exposure, audit logging, rate limits, encryption/retention and compliance before claiming production readiness.
9. **CI/deployment**: Run new GitHub Actions CI, fix any failures, then commit and push to `origin` only after reviewing diffs and ensuring no secrets or Person4 changes. No commit/push from this session yet.

## Immediate continuation order

1. `git status --short`, `git diff --check`, inspect the three agent outputs and current diffs.
2. Finish/reconcile agent work. Re-run Person1, Person2, Person3 tests and mock evals; deal with Windows test harness or use CI.
3. Verify Supabase `schema_migrations` contains `001`–`007`, bucket remains private, advisors and role grants still clean. Never apply `001`–`007` again to this same staging DB unless deliberately recovering.
4. Implement or at least prepare the hosted two-tenant isolation test, S3 smoke test, authentication design/integration and runtime deployment package. Keep Person4 untouched.
5. Commit/push reviewed changes and report exact verified status, then list only remaining external inputs (real keys, DNS/domain, AWS account, sender domain, etc.).

## User communication

The user favors informal tone, but asked for precision and complete work. Their AGENTS.md instructions request polished, structured technical reports for complex requests. Be explicit about what is live, local-only, untested, or blocked. Never say “production ready” while the auth, credential, hosting, storage smoke test, and two-tenant hosted test above remain open.
