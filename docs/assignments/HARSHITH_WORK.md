# Harshith — Remaining staging and deployment work

Updated 2026-10-24 after [PR #41](https://github.com/ryanshaon/FinLedger/pull/41), [#45](https://github.com/ryanshaon/FinLedger/pull/45),
[#47](https://github.com/ryanshaon/FinLedger/pull/47) and Ruthvik's post-Harshith changes merged into main (`4bbf722`).
This is your only active assignment file.

**Status: local work complete; hosted work not started.** Every §2a item is done locally. All hosted tasks in §2
are open and wait on Ryan's approvals.

## 1. Completed — do not rebuild

- Migration `012_ai_durable_state.sql`: durable retrieval/usage/reservation schema,
  tenant RLS, worker functions/grants and restricted per-firm automation maker.
- `extract-worker`: statement/key guards, extraction, safe failure categories,
  idempotent replay, retries/dead letters and lease-safe atomic status/score-job settlement.
- Compose extraction service; AI durability tests now use numbered migrations, not the removed SQL overlay.
- Native local smoke reached upload → extracted → score queued, with local EICAR scanning.
  This is the contributor's recorded local proof, not real ClamAV or hosted verification.

[Inspected CI](https://github.com/ryanshaon/FinLedger/actions/runs/39772975721):
**71 AI / 356 platform / 55 UI tests passed**, mock **3/3 + 6/6**, test/image jobs green.
These are CI results for the reviewed PR, not a new local run.

- PR #45 (documentation only): migration 012 staging rollout procedure and per-process runtime login
  table in the runbook; read-only/rollback-only checks in `docs/sql/migration_012_staging_checks.sql`;
  exact missing inputs and the Ruthvik integration contract below. Validated on local PostgreSQL 16 with
  a superuser owner only; no hosted state read or changed. CI green on `f7e8ca4`.

- PR #47: migration `013_llm_orphaned_holds.sql` (expired holds) and the extract-worker sweep call.
- Local completion PR (this update): durable `PostgresKillSwitch`/`PostgresUsageWriter` in `extract-worker`,
  migration `014_llm_unpriced_usage.sql`, the `score-worker` entry point, runbook/compose/staging-check updates.
  Local run on PostgreSQL 16: **165 AI / 369 platform / 71 UI passed**, mock **3/3 + 6/6**, 0 failed, 0 skipped.

**Local backend milestone and rollout preparation completed. Hosted operations are not completed.**
Migrations 012–014 are committed but documented as **not applied hosted**; verify actual history before any action.

## 2. Remaining work, in order

Read root [HANDOVER](../../HANDOVER.md), [deployment runbook](../DEPLOYMENT_RUNBOOK.md)
and [Auth design](../plans/AUTH_SUPABASE_DESIGN.md) first. Use synthetic data only.

| Task | Prerequisite | Completion evidence |
|---|---|---|
| Confirm current hosted state and access needs | Owner-approved access | Redacted checklist of schema/runtime/storage/Auth state; no secret values |
| Prepare migration 012 staging rollout | Independent SQL/security review and explicit hosted approval | Procedure, commit/blob and recovery plan done in PR #45. Still open: run Part A on staging (duplicate RAG refs, roles) and record the result |
| Apply migration 012 once, if approved and not already applied | Approved rollout and privately entered migration credentials | Recorded commit, migration-history result, schema/RLS/grant checks and rollback-only tenant proof |
| Create/verify restricted runtime login and worker roles | Owner supplies password privately; approved grants | Session-pooler connection uses non-owner/non-BYPASSRLS login; per-process least privilege and cross-tenant denial proven |
| Verify real private storage | Owner-private S3 configuration | Synthetic put/get/presign, unsigned-public denial and cleanup result; explicitly resolve any leftover test object |
| Verify real ClamAV | Reachable approved scanner/runtime | Healthy scan, test-signature rejection and fail-closed outage/retry behavior; EICAR stub alone does not count |
| Verify email delivery and inbound webhook | Verified sender domain, private keys and explicit synthetic-send approval | Synthetic outbox/webhook path, authentication rejection, retry/failure behavior and delivery evidence without payload logs |
| Deploy API, control UI and separate workers | Approved hosting account/budget/origins; compatible reviewed code | Immutable image/commit, HTTPS, safe proxy logging/limits, stable keys, process/dependency checks and monitoring |
| Verify recovery and hand off staging | Approved runtime plus tests above | Tested compatible-image rollback without deleting data; staging URL and safe synthetic setup for Ruthvik |

**Do now without live credentials:** prepare the rollout checklist, inspect code/role requirements,
review scanner/deployment configuration, identify exact missing inputs and agree integration PRs.
Do not wait silently or claim a hosted task complete because its local code exists.

**Local preparation (2026-10-22, no hosted access):** migration 012 rollout procedure and per-process login
table added to the [runbook](../DEPLOYMENT_RUNBOOK.md); checks in
[`migration_012_staging_checks.sql`](../sql/migration_012_staging_checks.sql), validated on local PostgreSQL 16 only.
No hosted state was read or changed. Exact missing inputs:

| Task | Missing input (owner: Ryan unless noted) |
|---|---|
| Hosted state check | Approved owner session to run Part A; current staging facts date from 2026-10-09 |
| Migration 012 | Named independent SQL/security reviewer, explicit apply approval and window, owner DSN entered privately |
| Runtime logins | Approval of the five-login layout, one private password per login, decision on `finledger_queue_ops` for the API |
| Private storage | Supabase Storage S3 access key pair in secret settings (bucket, endpoint and region are already known) |
| ClamAV | Approved place to run `clamd` reachable from workers; `FINLEDGER_CLAMD_HOST`/`PORT` |
| Email | Verified Resend domain, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, inbound domain/webhook secret/authserv id, synthetic-send approval |
| Hosting | Account, budget, region, API and control UI origins, stable `FINLEDGER_SIGNING_SECRET`/`FINLEDGER_ENC_KEY`, `SUPABASE_PUBLISHABLE_KEY`/`SUPABASE_SECRET_KEY` and Auth dashboard settings (Auth design §3.4, §6) |
| Extraction | Provider/model/location/retention/budget approval, then Ruthvik's transport; until then `extract-worker` refuses to start hosted |

## 2a. Next local work (no credentials needed)

- [x] **Check the 012 checks under Supabase-like permissions.** PR #45 ran them only as a local superuser.
  Rerun Parts A–C locally with a non-superuser owner that has `CREATEROLE` (as Supabase's `postgres` role does)
  and fix anything that fails. Part C uses `GRANT ... WITH SET`, which needs PostgreSQL 16+: record the staging
  server version as the first step of Part A and add a fallback or a clear stop for older versions.
  *Done locally:* passed 11/11 with a `LOGIN CREATEROLE` non-superuser owner on PostgreSQL 16.15; Part A now
  records the version, and Part C stops before writing on servers older than 16. Not yet run on Supabase.
- [x] **Recover stuck token holds.** *(merged in PR #47; Ruthvik accepted the `expired` rule, option 3)* A worker that dies between `llm_reserve_tokens` and settlement leaves a
  `held` row with no expiry, which blocks that bill's cap forever. Agree the approach with Ruthvik, then implement
  it as a new reviewed migration (013) or worker sweep, never by editing 012. Tests: expired hold released,
  live hold kept, cross-tenant sweep denied, replay safe.
  *Implemented:* migration `013_llm_orphaned_holds.sql` adds state `expired` (outcome unknown, kept for audit,
  not counted toward the cap) and `llm_expire_orphaned_tokens(client_id, document_id, job_id, claim_token)`.
  It needs the caller's live lease and expires only holds created before that claim, and does nothing while
  another job for the bill holds a live lease. `extract-worker` calls it before the model call. Four tests cover
  those cases plus the worker path. Ruthvik confirmed `expired` (not counted toward the cap) fits his accounting rules.
- [x] **Wire durable adapters into the worker.** *Done 2026-10-24:* `default_extract(conn)` builds a
  `PostgresKillSwitch` (per-bill cap and switch from `Settings`) injected into `ModelRouter`, and a
  `PostgresUsageWriter`, both on the worker's pooled connection with short `tenant()` transactions; usage settles
  on the hold's real reservation id. A refused hold (`ai_budget_exceeded`) moves the document to `exception`
  with reason `ai_budget_exceeded` and no score job. New migration `014_llm_unpriced_usage.sql` makes
  `llm_usage_rows.cost_usd` nullable with no default, so an unpriced call is stored with its tokens and NULL
  cost and its hold is reconciled instead of staying `held`. 012/013 are unchanged.
  *Known limit:* with `FINLEDGER_LLM_KILL_SWITCH=false` the router gets no real hold, so durable settlement
  would fail with `unknown_reservation`; keep the switch enabled wherever the durable path runs.
- [x] **Score consumer entry point.** *Done 2026-10-24 (Harshith took ownership after Ruthvik finished):*
  `finledger-platform score-worker` claims `score` jobs, locks the document, resolves the maker with
  `automation_maker_id()` (payload `maker_id` is ignored), and calls Person 3 `ControlService.score` in a savepoint
  in the same transaction as `finish_job`. Replays are skipped; a payload for another document becomes
  `score_payload_rejected`; an invoice Person 3 rejects becomes `canonical_invoice_rejected` with nothing
  half-written; other errors retry with fixed reasons; a lost lease rolls the score back. Review logic stays in
  Person 3. Login `fl_score` (roles `finledger_app`, `finledger_worker_score`) is in the runbook.
- [x] **Scanner and deployment config review.** Not covered by PR #45: review `clamd` settings, worker health
  checks and the staging process layout so they are ready as soon as hosting is approved. Record findings
  in the runbook; no hosted changes. *Done:* runbook §2 records clamd `StreamMaxLength` ≥ 40M, the 25 MiB
  bucket limit versus email attachments, private scanner network, per-process health checks and layout.

PR descriptions must not include "Generated by" attribution footers (PR #45's body had one); see §4.

**Do not** edit/reapply migrations 001–011, alter applied 012 SQL, apply uncommitted SQL,
reset hosted data, clear buckets or delete persistent volumes. Any additional fix uses a new reviewed
migration/PR. A real deployment cannot use mock AI; keep extraction disabled/fail-closed until Ruthvik's
approved live-provider/runtime path is ready. Do not weaken startup guards to make a demo work.

## 3. Coordinate with Ruthvik — do not duplicate his work

[Ruthvik's work](RUTHVIK_WORK.md) covers provider/accounting fixes, actual HTTP/runtime wiring,
durable-adapter integration, score/map → review completion and detailed Auth/UI/end-to-end QA.
*2026-10-24:* his adapter/router injection, schema alignment and maker/editor fixes are on main; the worker
wiring and score consumer above are done. What remains with him is the hosted Auth/UI/end-to-end QA.

- Give him migration/function signatures, the extract result/score payload contract
  (`canonical_invoice`, `maker_id`), reviewed commit links and operational dependency results.
- Coordinate who implements any score-consumer platform entry point; Ruthvik owns completing
  and verifying the scoring-to-review integration. Do not start a competing implementation.
- Support wiring `PostgresKillSwitch` and `PostgresUsageWriter` into the actual worker.
  Current `default_extract()` still uses in-memory usage; agree short tenant transactions
  and atomic usage/hold settlement with Ruthvik, not a transaction held across model calls.
- Support independent automation-maker/human-editor checks. The established requirement is
  that automation must not let a human editor approve their own work. Do not relax that
  requirement or invent automatic posting/payment permission.
- Supply the approved staging URL and private synthetic test setup. Ruthvik leads real Auth,
  browser/workflow and upload-to-review proofs; you fix backend/operational failures they expose.

**Integration contract for Ruthvik (written at main `4fc1fe2`; the three open points below were resolved by
main `4bbf722` and this update, kept for the record):**

- **Functions (migration 012).** All `security definer`, callable only by a login whose `session_user` is in
  `finledger_worker_extract`/`score`/`map`, and only inside `tenant()` for the same client:
  `llm_reserve_tokens(client_id uuid, document_id uuid, reservation_id uuid, estimated_tokens int, cap int) → void`
  (locks the document row; raises `ai_budget_exceeded`, `tenant_mismatch`, `document_not_found`, `invalid_cap_arguments`),
  `llm_reconcile_tokens(reservation_id uuid) → void` (held → reconciled, else `unknown_reservation`),
  `llm_release_tokens(reservation_id uuid) → void` (no-op if unknown or settled),
  `automation_maker_id(client_id uuid) → uuid`. Usage is a direct RLS-checked insert into `llm_usage_rows`.
- **Score job.** Queue `score`, one per document (`idem_key ''`), enqueued in the same transaction that moves
  `received → extracted` and writes the `extracted` workflow event as the maker. Payload:
  `{document_id, client_id, maker_id, canonical_invoice}`, where `canonical_invoice` is Person 1's
  `CanonicalInvoice.model_dump(mode="json")`. Person 3 `ReviewService.score(..., actor_id=maker_id)` then stores it
  as `canonical_invoices.maker_id`. **Mismatch:** a mock extraction is rejected by `validate_invoice` with
  `vendor.pan`/`vendor.state` `extra_forbidden` (schema drift) plus `total`/`line_items` (mock content). The
  consumer must align the two schemas; Ruthvik decides where, without loosening validation silently.
- **Durable adapters, not yet wired.** `ModelRouter` always builds an in-memory `KillSwitch` (no injection
  point) and `default_extract()` passes an in-memory `UsageWriter`. As written, `PostgresKillSwitch` and
  `PostgresUsageWriter` need the caller's open `tenant()` transaction, which would span the model call, and
  `PostgresUsageWriter` stores a random `reservation_id`, so usage is not tied to its hold. Proposed split, to
  agree before code: Ruthvik makes the adapters open their own short `tenant()` transaction per call, settles
  usage insert + `llm_reconcile_tokens` in one transaction keyed by the real reservation id, and adds a router
  injection point; Harshith then wires them into `default_extract()` with the worker pool. Open question: a
  worker that dies between reserve and settle leaves a `held` row with no expiry, which counts against that
  bill's cap until released; recovery needs an agreed sweep or a new reviewed migration.
- **Automation maker vs human editor.** With the automation as maker, Person 3 `approve()` only compares the
  approver with `canonical_invoices.maker_id`. A human who submitted the draft (`voucher_drafts.created_by`)
  or edited it (`correction_events.actor_id`) can therefore approve their own work. That breaks the
  established rule. The fix is in Person 3 approval checks (Ruthvik) and needs no schema change; no posting or
  payment permission changes.

Ryan's [short checklist](RYAN_WORK.md) remains approvals/private access and final acceptance,
not implementing hosted setup or repeating detailed QA.

## 4. Approval gates and reporting

Provider/model, processing location, retention, AI budget/capacity and hosting account/budget
still require approval. Private credentials belong in secret settings, not Git, chat or PR output.
No model calls, real invites, outbound messages or hosted mutations are authorized by this file alone.

Keep this file updated in place: check off verified work or move it to the concise completed record.
Each PR/HANDOVER update lists exact commands/pass/fail/skip counts, environment, cleanup,
actual hosted changes and named blockers. Require green CI on the exact final commit and
independent security review. P2 dependency changes require both P2/P3 lockfiles.

No RLS/MFA/scanner bypass, force-push cleanup, AI attribution trailers or `person4_tally/` edits.
Do not claim production readiness while integration or hosted verification remains blocked.
