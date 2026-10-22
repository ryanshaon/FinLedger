# FinLedger — Combined backend, AI handover and release assignment

Prepared 2026-10-22. Repository: https://github.com/ryanshaon/FinLedger

## 1. Your objective

Own the remaining P1–P3 integration, staging setup, controls/UI regression work and release
verification. Reuse existing implementations; deliver a reproducible synthetic invoice journey
from upload to human review. Customer launch is a separate owner decision. Do not edit `person4_tally/`.

This is the single combined brief for the contributor handling both `NEXT_PERSON_HANDOVER.md`
and `02_FRIEND_PLATFORM.md`. It also assigns that contributor implementation, detailed
controls/UI testing and QA previously listed under Ryan. The older files are pointers,
not separate assignments. Check active PRs before coding and reuse completed work.

## 2. Read first and establish the baseline

Read [HANDOVER](../../HANDOVER.md) completely for mandatory safety rules and owner gates,
then [the schema proposal](../proposals/AI_DURABLE_RETRIEVAL_USAGE_SCHEMA.md),
[the deployment runbook](../DEPLOYMENT_RUNBOOK.md),
[Auth design](../plans/AUTH_SUPABASE_DESIGN.md) and
[provider proposal](../decisions/ADR-001-AI_PROVIDER.md).
Older handover/audit statements that no provider or durable adapters exist are superseded by
PR #37: the adapters exist, but live network transport and production integration remain unfinished.

Latest inspected main before this brief: `cf5d2f0`, PR #37. Its October 20 CI reported
**71 AI / 333 platform / 55 UI tests passed**, mock **3/3 structural + 6/6 exact**.
These are recorded CI results, not a new local run or proof of hosted configuration.

### Already delivered — reuse, do not rebuild

PR #37 imported the AI contributor's schema proposal, synthetic fixtures/fail-closed regressions,
credit-note fixture, stable extraction failure categories, Gemini adapter with fake transport,
durable Python adapters tested on ephemeral PostgreSQL and an opt-in live-evaluation gate.
Both mock and Gemini remain blocked in staging/production pending approved activation.
No live provider or hosted durable migration was delivered by that work.

| Boundary | Existing source |
|---|---|
| Extraction entry point/failures | `packages/extract/contract.py`, `packages/extract/failures.py` |
| Statement/vision and bounded inputs | `packages/extract/vision_fallback.py`, `packages/extract/input_policy.py` |
| Gemini adapter/transport interface | `packages/usage/providers/`, `packages/usage/model_router.py` |
| Durable retrieval | `packages/rag/postgres_indexer.py` |
| Reservations/usage | `packages/usage/postgres_kill_switch.py`, `packages/usage/postgres_usage_writer.py` |
| Test-only SQL/durability tests | `packages/testing/sql/ai_durable_ephemeral.sql`, `packages/usage/tests/test_durable_postgres.py` |
| Evaluation | `evals/run_evals.py` (`--live-synthetic-eval` stays opt-in) |

Use Python 3.12, uv and PostgreSQL tools; Docker is needed for the Compose stack. From your clone:

```powershell
git switch main
git pull --ff-only
git switch -c feat/release-integration-baseline
powershell -ExecutionPolicy Bypass -File scripts/test.ps1
```

Record actual results, tool/setup gaps and skipped checks. Never use a hosted database as the test-owner DSN.

## 3. AI runtime integration

- [ ] Inspect the existing Gemini adapter, `GeminiTransport` interface, fake transport,
  `PostgresIndexer`, `PostgresKillSwitch`, `PostgresUsageWriter` and extraction contract.
- [ ] Prepare network-transport tests with fake HTTP, fixed diagnostic categories, explicit
  location/model, timeouts, bounded retries/payloads and safe authentication handling.
- [ ] After remaining policy gates are approved, complete the actual provider transport and
  runtime wiring. Do not simply remove the staging/production guard to get a demo running.
- [ ] Bind durable retrieval/accounting to the runtime using the reviewed platform schema;
  prove restart recovery, non-owner RLS, cross-tenant denial and concurrent reservation limits.
- [ ] Keep live evaluation opt-in. Run synthetic live extraction only with explicit approval;
  record model/location, measured usage/cost and remaining limitations without invoice payloads.

**Acceptance:** no silent fallback to mock or another provider/location; no provider call before
policy/configuration validation; durable accounting cannot be bypassed by restart or another worker.
Revalidate the dated model/lifecycle/pricing proposal before any approved activation.

## 4. Integrate the platform handover

Own the AI handover and platform tasks together. Check active PRs first; deliver one
reviewed migration/consumer implementation, not parallel versions from the two older briefs.

- [ ] Review/merge the next numbered platform migration for tenant-safe retrieval, usage and
  atomic reservations. Test against fresh and upgraded local databases. Test-only overlay SQL
  is not a deployment migration; inspect existing rows before adding uniqueness constraints.
  Implement the reviewed `(client_id, ref)` RAG upsert key, usage/reservation tables,
  tenant RLS and worker grants, plus reserve/reconcile/release token functions. Coordinate
  the next available migration number after `011`; do not assume it remains unused.
- [ ] Integrate `extract-worker` using existing claims/leases, tenant context, input loader and
  `extract_document(...)`; wire successful results into scoring/mapping and human review.
  Use `load_extract_input`, `tenant(conn, client_id)` and stable `ExtractFailure` categories.
  Keep invoice data out of `last_error`; Person 1's contract does not supply an actor identity.
- [ ] Ensure statements become explicit exceptions without a model call; reject object keys
  outside the current tenant prefix before storage reads or AI invocation.
  Preserve `statement_not_extracted` and `object_key_rejected` diagnostic handling.
- [ ] Implement or integrate one non-login automation maker per firm: no Auth subject, bearer
  token or admin privilege. Validate human-editor/maker-checker separation with an independent reviewer.
- [ ] Prove idempotent replay, compare-and-set status updates, bounded retries/dead letters,
  lease expiry/late-result rejection, atomic settlement and invoice-free queue diagnostics.

**Acceptance:** synthetic intake reaches review once, without tenant leakage or duplicate scoring.
Failure/recovery cases have regression tests. Extraction never authorizes posting or payment.

## 5. Staging operations and dependency proofs

- [ ] Verify what is actually deployed before changing it; current hosted state has not been
  freshly inspected for this brief. Preserve existing data and private configuration.
- [ ] With owner-approved access, verify/create the restricted runtime login and appropriate
  worker grants using the session pooler. Never run application queries as owner/service role.
- [ ] With private owner configuration, prove S3 put/get/presigned access, unsigned-public denial
  and cleanup. Record leftovers explicitly; never clear a bucket or delete volumes as cleanup.
- [ ] Verify ClamAV readiness and fail-closed behavior; configure safe ingress/proxy logging,
  HTTPS, request limits, stable encryption/signing keys and worker operational checks.
- [ ] Test Resend delivery and inbound webhooks with synthetic messages after approval;
  do not blindly replay outbox items or expose signed/invite URLs in logs.
- [ ] Deploy API, control UI and separate workers only to an approved hosting account/budget.
  Document immutable image version, dependency checks, monitoring and safe rollback.

**Acceptance:** dependency proofs and cleanup have actual results; health endpoints alone do not
count as storage/Auth/email/scanner verification. Document any hosted configuration/SQL changes.
Migrations 001–011 were already applied in the last verified staging state: do not reapply/edit
them. Commit exact new migration SQL and obtain review before approved hosted execution.

## 6. Controls, UI and end-to-end QA

- [ ] Own review/correction/rejection, duplicate-submission and invalid-form regression tests.
  Make small usability/error-display fixes; preserve authorization and workflow rules.
- [ ] Present queued, failed, budget-exhausted and excluded-statement states accurately;
  never show processing success solely because a job was accepted.
- [ ] Verify invite → set password → login → TOTP → client list → logout, first with fake Auth,
  then synthetic staff on approved staging. Test AAL1 denial, old-cookie rejection/revocation,
  cross-client denial and 8-hour absolute / 30-minute idle session expiry. SSO remains deferred.
- [ ] Run synthetic upload → extract → score/map → correction/review with negative cases:
  scanner outage, provider timeout, malformed output, cap exhaustion, lost lease and retries.
- [ ] Record bugs with steps/expected/actual result and redacted evidence; fix affected callers,
  fixtures and tests together. Obtain independent review for security-sensitive changes.

**Acceptance:** reproducible browser/workflow tests and a release evidence report with exact
pass/fail/skip counts, commit, environment, cleanup, unresolved blockers and rollback instructions.

## 7. Work order and completion rules

1. Baseline tests and active-PR check; start local transport/control regression work.
2. Deliver the reviewed schema/consumer; wire adapters and maker behavior.
3. Resolve owner gates, provision only approved resources, then verify staging dependencies/Auth.
4. Complete invoice/failure E2E, fix regressions, request independent review and deliver release evidence.

Work in small branches/PRs; green CI must cover the exact integrated commit. Coordinate shared
contracts, migration numbers and root configuration. P2 dependency changes require both P2/P3 lockfiles.
Update HANDOVER with exact results and unfinished branches before stopping. Never commit secrets,
share `.env`, weaken RLS/MFA/scanning, force-push shared history or add attribution trailers.

Keep the existing layout: `packages/` + `evals/` for AI, `person2_platform/` for runtime/migrations,
`person3_control_ui/` for controls and tests, and `docs/` for plans/evidence. No new parallel architecture.
Defer speculative indexing until real query plans exist. Person 4 stays separately owned and untouched.
