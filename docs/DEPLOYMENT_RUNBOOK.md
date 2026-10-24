# FinLedger release and rollback runbook

Operational checklist for the owner and deployment operator. Scope: Persons 1–3 only.
This is a procedure, not evidence of a deployment or approval to provision paid resources.

## 1. Release gates

**Customer release is blocked.** Read [HANDOVER.md](../HANDOVER.md) for current owner actions and
[the Person 1 audit](audits/PERSON1_PRODUCTION_READINESS.md) for the missing provider, extract consumer,
persistent retrieval/accounting and data-handling decisions. Do not route customer invoices through mock AI.
Person 4 is outside this runbook; no vendor-to-Tally completion is claimed.

Before a staging release, record the following without secret values:

- Reviewed commit SHA, passing `test` and `image` CI links, image digest, and operator.
- Approved hosting account/budget, service origins and deployment window.
- Previous known-safe image digest, compatible schema versions and tested recovery procedure.
- Private runtime configuration, non-owner app login role and reachable scanner.
- Auth settings and private server keys described in [the Auth design](plans/AUTH_SUPABASE_DESIGN.md).
- Owner-approved AI provider/region, permitted invoice data flows, retention and logging policy before
  implementing or enabling extraction.

Do not assume a successful container build proves the database, storage, email or Auth configuration works.

## 2. Process layout and configuration

One image supplies separate processes. Do not run a worker inside a web process.

| Process | Command | Operational check |
|---|---|---|
| Intake API | `finledger-platform serve --host 0.0.0.0 --port 8000` | `/healthz` plus a synthetic intake test |
| Control UI | `finledger-control --host 0.0.0.0 --port 8770` | `/healthz`, sign-in and MFA; set container `PORT=8770` |
| Ingest worker | `finledger-platform worker` | Scanner availability, queue progress, retries and dead jobs |
| Email worker | `finledger-platform outbox-worker --client-id <client-uuid>` | One approved client per process; delivery and failed outbox rows |
| Extract worker | `finledger-platform extract-worker` | Extract retries/dead jobs, document exceptions, `score` queue depth |
| Score worker | `finledger-platform score-worker` | Score retries/dead jobs, `canonical_invoice_rejected` exceptions, `score` queue age |

The extract worker needs migration 012 and an approved provider. On `main` at `4fc1fe2`, `ModelRouter`
refuses both `mock` and `gemini` when `FINLEDGER_ENV` is staging/production, so `extract-worker` exits at
startup there; do not deploy it until the approved live-provider path lands. With a pooled connection it
reserves and settles tokens durably (`PostgresKillSwitch`/`PostgresUsageWriter`, one short transaction per step);
a bill whose cap is spent becomes a document exception (`ai_budget_exceeded`), not a dead job.
The score worker runs Person 3's `ControlService.score` as the firm's automation maker and moves
`extracted → scored`; an invoice Person 3 rejects becomes a document exception, never a retry loop.

Runtime database logins (one per process; names are examples, passwords are owner-chosen secrets).
Each login is `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB INHERIT`, owns no tables, and connects
through the session pooler. Queue functions check `session_user`, so grant roles to the login itself.

| Process | Login | Member of | Notes |
|---|---|---|---|
| Intake API | `fl_api` | `finledger_app` | Add `finledger_queue_ops` only if firm admins should use the dead-job requeue route |
| Control UI | `fl_control` | `finledger_app` | |
| Ingest worker | `fl_ingest` | `finledger_app`, `finledger_worker_ingest` | |
| Extract worker | `fl_extract` | `finledger_app`, `finledger_worker_extract` | Only after migrations 012–014 and an approved provider |
| Score worker | `fl_score` | `finledger_app`, `finledger_worker_score` | Needs 012 (`automation_maker_id`) |
| Email worker | `fl_outbox` | `finledger_app` | Row locks, no queue claim |
| Migrations | owner | | One-off restricted environment only |

Compose grants every worker role to one local `fl_app`; that is a local convenience, not this layout.
The image's HTTP health check is for web processes; disable/replace it for workers, as local Compose does.
Intake `/healthz` checks database connectivity with `SELECT 1`; Control UI `/healthz` checks process
liveness only. Neither proves storage, scanner, email, Auth, schema compatibility or tenant isolation.

Runtime rules:

- Set `FINLEDGER_ENV=staging` or `production` explicitly. Keep HTTPS end-to-end at the browser boundary;
  configure trusted proxy forwarding for the actual deployment. Never broadly trust arbitrary client proxies.
- Supply `FINLEDGER_DATABASE_URL` privately using the approved non-owner login through the Supabase
  **session pooler**. Grant only the roles needed by each process. Never use an owner DSN for runtime queries.
- Owner migration credentials belong only in a restricted one-off migration environment, not web/worker
  environments. Never echo DSNs, API keys, passwords, invite tokens or credential bundles.
- Use the private S3 bucket and settings in HANDOVER owner checklist 3; S3 credentials are not Supabase
  publishable/secret API keys. Keep the bucket private and run the real smoke proof before declaring it ready.
- Use `FINLEDGER_VIRUS_SCANNER=clamd`; EICAR mode detects only a test signature and is not antivirus.
  A scanner outage must leave documents unscanned and retry ingestion, never permit extraction.
  Scanning shares a 30-second monotonic socket-I/O budget across connection, upload and response.
  Late results fail closed. System DNS/multiple-address connection attempts can exceed the socket timeout;
  this is not a guaranteed wall-clock interrupt. Configure reachable private scanner endpoints and monitor
  queue age/retries; do not bypass scanning to clear a backlog.
- Enforce ingress body-size, request-duration and concurrency limits at the deployment boundary.
  The email webhook buffers at most 40 MiB before parsing, but that is not a total ingress concurrency limit.
  It borrows a database connection only after valid HMAC authentication and recipient parsing; intake and
  receipt creation share a tenant transaction in a worker thread. Real inbound-provider delivery remains
  unverified until the owner supplies its private settings and approves a staging proof.
- Browser Auth needs `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and `FINLEDGER_ENC_KEY`.
  `SUPABASE_SECRET_KEY` is for the control server's invite path only, never browser code or database tenancy.
- Resend needs a verified domain, `RESEND_FROM_EMAIL` and private `RESEND_API_KEY`. Failed messages require
  manual review; there is no fleet-wide outbox scheduler or automatic retry policy.
- Keep encryption/signing keys stable across replicas and releases. Rotation needs its own reviewed plan;
  replacing the Fernet key makes existing encrypted credentials unreadable.

Scanner and process configuration, reviewed against code on `main` (2026-10-22, no hosted check):

- **clamd stream limit.** The ingest worker sends each stored document to clamd in one `INSTREAM`. Portal
  uploads are capped by `FINLEDGER_MAX_UPLOAD_BYTES` (25 MiB), but an email attachment is bounded only by the
  40 MiB webhook buffer. Set clamd `StreamMaxLength` to at least 40M, or the attachment gets a size-limit reply,
  which is treated as a scanner failure: retries, then a dead job, never a pass. Larger documents also need
  the shared 30-second scan budget; test the largest synthetic size before go-live.
- **Bucket limit.** `finledger-documents-dev` has a 25 MiB object limit, so an email attachment over 25 MiB
  cannot be stored. Raise the bucket limit or cap attachments in intake (a reviewed code change); decide before
  enabling inbound email.
- **Scanner reachability.** Run clamd on a private network only (port 3310 never public), with signature
  updates and enough memory for the signature database. Workers start before signatures load and retry
  (fail closed); watch ingest retries after every scanner restart.
- **Health checks.** The image `HEALTHCHECK` targets `/healthz` on `PORT` (default 8000). Set `PORT=8770` for
  the control UI, and disable or replace the check for every worker. Hosting platforms that ignore Docker
  `HEALTHCHECK` need their own per-process check. Workers have no health endpoint: monitor queue age, retries
  and dead jobs instead.
- **Layout.** Separate processes per the table above. Run one email worker per approved client, and don't run
  `extract-worker` hosted yet. `FINLEDGER_ENV=staging` already refuses a non-`clamd` scanner at startup.

Do not publish raw request bodies or sensitive URL paths/query strings in access logs. Invite links,
signed object URLs and vendor upload links contain credentials even though they are not labelled API keys.
Both built-in CLI launchers disable Uvicorn access logging. A custom ASGI launch configuration must do the
same or redact those URLs before logging; reverse-proxy/load-balancer logs require independent configuration.

## 3. Verification order

### Offline and local checks

Run the repository's established full test command on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test.ps1
```

Current exact counts and CI evidence belong in HANDOVER, not a permanently assumed baseline here.
Database tests require temporary local PostgreSQL; do not point test-owner configuration at hosted data.

For a **local-only** container stack, with Docker available:

```text
docker compose --profile scan up --build
```

The `scan` profile starts ClamAV. Without it, Compose still points ingestion at `clamav`, which is not running;
the worker must fail closed. Wait for scanner signatures/readiness before testing ingestion. Compose uses
development placeholder secrets and broad local worker-role grants; it is not a production deployment template.
Do not delete volumes as part of a routine restart or rollback.

### Staging proofs, after owner setup

Use privately loaded configuration; never place real secret assignments in copied commands or chat.

```text
finledger-platform isolation-check
finledger-platform s3-smoke
```

- Isolation proof uses a restricted operator owner connection and rolls back its test data. This is not an
  instruction to run the application as owner. Record actual pass/fail output and the target environment.
- S3 proof writes synthetic bytes, verifies read/presigned access and unsigned-public denial, then attempts
  deletion in `finally`. Record the cleanup result; do not claim no object remains if cleanup failed. Resolve
  a leftover synthetic object explicitly, not by clearing a bucket.
  The proof accepts only unsigned HTTP 401, 403 or 404 as denial; bad requests, throttling and service failures
  fail the check. These correspond to authorization/not-found categories in the
  [Supabase storage error reference](https://supabase.com/docs/guides/storage/debugging/error-codes).
  Require a verified private bucket and the correct public-object endpoint as well. Legacy deployments
  returning HTTP 400 need explicit operator investigation; the tool does not infer privacy from that status.
  Normal step exceptions produce fixed, credential-free failed labels and still return the cleanup report.
  Cleanup failure includes only the generated synthetic object key for explicit operator recovery.
  Interrupted processes or missing output still require independent cleanup verification. With no custom
  endpoint configured, the public-object check is skipped; successful S3 roundtrip alone does not prove privacy.
- Migrations 001–011 are already applied to the current Supabase staging project. Do not reapply or edit
  them. New SQL must be committed in the migrations directory and reviewed/tested before hosted execution.

### Migration 012 staging rollout

Prerequisites: independent SQL/security review, Ryan's explicit approval and window, owner DSN entered
privately in a one-off migration shell, and no web/worker process running against staging.
Checks live in [`sql/migration_012_staging_checks.sql`](sql/migration_012_staging_checks.sql) (read-only
plus one rollback-only proof; psql or the SQL editor, as owner).

1. **Identify.** Merged at `1c7d8a0` (authored in `599bb58`); Git blob
   `3cf8d16f9c0be2d6e116a3395b19c80cfd18cc8a` (`git rev-parse <commit>:person2_platform/src/finledger_platform/migrations/012_ai_durable_state.sql`).
   Deploy only images built from a commit with the same blob. Migration `013_llm_orphaned_holds.sql`
   (blob `6a0fdf8d5e0ce39a76000f87404bbace72763800`, adds the `expired` hold state and `llm_expire_orphaned_tokens`) ships with 012 in
   any image built after it merged, and so does `014_llm_unpriced_usage.sql` (blob
   `29f811e3d31e6278e5905bfa2bab831eb704b807`, makes `llm_usage_rows.cost_usd` nullable: NULL means an unpriced
   call, never free). Review and approve the three together.
2. **Part A, before.** Expect `applied` to list exactly `001`–`011`, no 012 tables or `users.automation`,
   `dup_groups = 0`, `maker_email_clashes = 0` and all five roles present. Any difference: stop. Duplicates
   abort 012 by design; resolving them is a separate approved data task.
3. **Apply once** with `finledger-platform migrate` (owner DSN). It runs each pending file in one transaction
   with its `public.schema_migrations` row, so it must report exactly the approved pending files, in order:
   `applied: ['012_ai_durable_state.sql', '013_llm_orphaned_holds.sql', '014_llm_unpriced_usage.sql']`.
   Anything else: stop. Do not use Supabase `apply_migration` for these files: it would not write
   `public.schema_migrations`, and a later `migrate` would replay them and fail. Supabase's own history will not list 012; record that, do not hand-edit it.
4. **Part B, after.** Expect RLS on both tables; table grants only `finledger_app` SELECT on usage, worker
   roles INSERT on usage and SELECT on reservations; EXECUTE on all five functions (four from 012,
   `llm_expire_orphaned_tokens` from 013) only for the three worker roles
   (not `anon`, `authenticated`, `service_role` or `finledger_app`); `cost_usd` nullable with no default;
   one automation maker per firm; no empty refs.
5. **Part C, proof.** Expect 11 `passed = t` rows and `nothing_persisted = t`.
6. **Recovery.** A failed apply rolls back entirely. After a successful apply there is no down migration:
   pre-012 images ignore the new objects, and `create-firm` still works because the trigger adds the maker.
   Any correction is a new reviewed migration, never an edit of 012.
7. Record commit, blob, the three outputs and the operator in HANDOVER, without connection strings.

Local evidence for the procedure (2026-10-22, PostgreSQL 16.15): with a superuser owner, 001–011 recorded
first, `migrate` applied only 012, Parts A–C matched the expectations above, and a negative control (RLS off,
extra EXECUTE grant) turned three proof rows false. Repeated with a Supabase-like owner (`LOGIN CREATEROLE`,
not superuser, no `BYPASSRLS`, creator of all FinLedger roles): 001–011 applied as that owner, `migrate`
applied 012, Part C passed 11/11 and nothing persisted. Part C stops before writing on PostgreSQL < 16
(checked by forcing the threshold). Not yet run against Supabase itself.
- Do not automate `create-firm` in captured agent output: that command prints a bearer API token. Owner-led
  bootstrap needs a private terminal and explicit Auth-subject linking, never automatic linking by email.

Run the real invitation → set password → sign-in → TOTP → client-list flow using synthetic staff/test firms
only after owner Auth configuration and a reachable runtime exist. Check that AAL1 cannot access client data,
the old cookie is retired after MFA, cross-tenant access is denied, and logout revokes the local session.
Old encrypted session bundles without local MFA assurance require MFA again; this is intentional.

## 4. Staged release and monitoring

1. Confirm no unexpected remote changes; deploy the reviewed immutable image to staging first.
2. Complete dependency proofs and real Auth E2E. Keep customer intake disabled while release gates remain.
3. Record queue depth/age, ingest retries/dead jobs, scanner health, web errors/latency, Auth failures and
   outbox failed state. Current `/healthz` is not a substitute for these checks.
4. Owner/operator must approve the observation window and latency/error thresholds from measured baselines.
   Do not invent service-level objectives or treat an empty database's index statistics as performance proof.
5. Only a separately approved customer release may proceed. Do not run paid provisioning, real invites,
   provider calls or outbound email just because this checklist exists.

Immediate stop conditions: cross-tenant exposure, Auth/MFA bypass, raw secret/invoice leakage, public bucket
exposure, scanner bypass, or unexpected posting. Pause the affected ingress/process and escalate to the owner.
Retain redacted diagnostics and incident times, not invoice contents or credentials in tickets.

## 5. Rollback and recovery

- Freeze new intake and stop affected workers before reverting an image. Avoid completing or replaying jobs
  under an incompatible version. Allow normal lease expiry; do not delete jobs to make monitoring look clean.
- Use only a previous **known-safe, schema-compatible** digest. Do not roll back to a build that lacks the
  worker diagnostic privacy, malformed-form or MFA cookie-assurance fixes (PRs #18, #21, #22).
- Keep database/object data and stable encryption keys. This repo has no automatic down-migration path:
  an image rollback is not a database rollback. Never drop/reverse migrations or restore a backup over live
  state without a separately approved, tested recovery plan.
- Migration 012 is additive (new tables/functions, `users.automation` and one maker row per firm) and is not
  reversed by an image rollback. Images without `extract-worker` leave `extract` jobs queued, not lost.
- Review outbox acceptance before any resend. Provider idempotency has a time window; delivery is not
  exactly-once, and blind replay can duplicate email. Dead-job requeue requires the queue-operations role and
  a resolved cause; do not blanket-requeue.
- Re-run liveness, dependency and access-boundary proofs on the recovered release before reopening ingress.
- Record release/rollback digests, timings, checks, cleanup, remaining failures and any hosted SQL/configuration
  changes in HANDOVER. Commit and push all implementation/WIP work using the mandatory hand-back protocol.

## 6. Implementation order and repository references

Owner credentials/configuration → approved staging runtime → storage/RLS/Auth proofs → approved provider and
consumer/persistence implementation → real-document staging E2E → separately approved customer release.

```text
HANDOVER.md                     current blockers, exact verification, next-agent task
Dockerfile / compose.yaml       shared image and local-only process example
.github/workflows/ci.yml        test and image checks
docs/DEPLOYMENT_RUNBOOK.md       release/recovery procedure
docs/plans/AUTH_SUPABASE_DESIGN.md
docs/audits/PERSON1_PRODUCTION_READINESS.md
person2_platform/               runtime, proofs and committed migrations
person3_control_ui/             staff Auth, review and workflow controls
```
