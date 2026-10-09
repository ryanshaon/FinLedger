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

There is **no extract-worker command** yet. Starting ingest alone leaves jobs awaiting the missing consumer.
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
