# Harshith — Backend platform and AI handover

Prepared 2026-10-22. Repository: https://github.com/ryanshaon/FinLedger

## 1. Scope and coordination

This combines only `NEXT_PERSON_HANDOVER.md` and `02_FRIEND_PLATFORM.md` for Harshith.
Ruthvik keeps [his existing release-integration assignment](RELEASE_INTEGRATION.md), including
live AI transport/runtime wiring, controls/UI work and detailed QA previously assigned to Ryan.
Do not take over that assignment or build a second provider implementation.

| Surface | Harshith delivers | Ruthvik handles |
|---|---|---|
| Database | Reviewed durable-schema migration, RLS and grants | Connects AI adapters and verifies integration |
| Extraction queue | Consumer, leases/status settlement, statement/key guards | Supplies live AI runtime integration and verifies full workflow |
| Automation maker | Restricted per-firm maker, backend workflow integration | Controls/UI and independent maker-checker verification |
| Operations | Restricted runtime login, storage/email/scanner proofs and approved deployment | Cross-service acceptance, Auth/browser QA and release evidence |

Agree PRs and interfaces before touching shared files. Ruthvik's references to integrating,
reviewing or proving platform behavior mean consuming your implementation, not duplicating it.
Obtain independent security review; one contributor's self-test is not independent approval.

## 2. Baseline and first steps

Read root [HANDOVER](../../HANDOVER.md) completely, the
[schema proposal](../proposals/AI_DURABLE_RETRIEVAL_USAGE_SCHEMA.md),
[deployment runbook](../DEPLOYMENT_RUNBOOK.md) and [Auth design](../plans/AUTH_SUPABASE_DESIGN.md).

PR #37 already delivered synthetic extraction/vision regressions, credit-note fixtures,
safe failure categories, a Gemini adapter tested with fake transport, durable Python adapters
tested on ephemeral PostgreSQL and an opt-in live-evaluation gate. Reuse these components.
No hosted durable migration or live provider activation was delivered by that work.
October 20 CI: **71 AI / 333 platform / 55 UI passed**, mock **3/3 + 6/6**; historical evidence only.

With Python 3.12, uv and PostgreSQL tools, from your clone:

```powershell
git switch main
git pull --ff-only
git switch -c feat/platform-integration
powershell -ExecutionPolicy Bypass -File scripts/test.ps1
```

Own `person2_platform/`, migrations, Docker/Compose and operational configuration.
Coordinate contracts, root CI and dependencies with Ruthvik. Never point test-owner DSNs at hosted data.

## 3. Backend deliverables, in order

- [ ] Verify the existing local stack, scanner readiness and queue replay/lease behavior.
- [ ] Author the next available numbered migration after `011` from the schema proposal.
  Include `rag_chunks` uniqueness on `(client_id, ref)`, usage rows, reservations,
  reserve/reconcile/release functions, tenant RLS and least-privilege worker grants.
  Inspect existing data before uniqueness changes; test fresh and upgraded databases.
  Test-only SQL in `packages/testing/sql/ai_durable_ephemeral.sql` is not a hosted migration.
- [ ] Build `finledger-platform extract-worker`: claim `extract`, use `tenant(conn, client_id)`,
  `load_extract_input` and `extract_document(...)`; coordinate successful extraction into
  scoring/mapping/review with Ruthvik. Keep model calls outside long database transactions.
- [ ] Set statements to an explicit document exception without a provider call
  (`statement_not_extracted`). Reject object keys outside `{client_id}/` before reading
  storage or invoking AI (`object_key_rejected`).
- [ ] Implement one non-login automation maker per firm with no Auth subject, token or admin
  privilege. Agree human-correction/maker-checker semantics with Ruthvik; no automatic
  posting/payment authority is granted by the maker choice.
- [ ] Prove idempotent replay, compare-and-set statuses, retry/backoff/dead letters,
  lost-lease/late-result rejection and atomic settlement; `last_error` uses safe categories only.
- [ ] Under approved access, verify/create the restricted app login and worker grants via
  the documented session pooler. Prove tenant isolation without owner/service-role runtime access.
- [ ] With owner-private configuration and approval, prove storage roundtrip/presigned access,
  unsigned-public denial and cleanup; verify ClamAV, email delivery and inbound webhook behavior.
- [ ] Deploy API/UI/separate workers only to an approved hosting account/budget. Configure HTTPS,
  safe logs/request limits and stable encryption/signing keys; document image/version and rollback.
  Give Ruthvik the staging URL and safe test setup for his acceptance work.

## 4. Interfaces you must reuse

| Boundary | Source |
|---|---|
| Extraction function/failures | `packages/extract/contract.py`, `packages/extract/failures.py` |
| Statement/vision behavior | `packages/extract/vision_fallback.py` |
| Durable retrieval | `packages/rag/postgres_indexer.py` |
| Token reservations/usage | `packages/usage/postgres_kill_switch.py`, `packages/usage/postgres_usage_writer.py` |
| Fake-tested Gemini boundary | `packages/usage/providers/`, `packages/usage/model_router.py` |
| Durability tests/reference SQL | `packages/usage/tests/test_durable_postgres.py`, `packages/testing/sql/ai_durable_ephemeral.sql` |

Person 1's extraction contract does not supply a maker actor ID. Runtime mapping belongs in
the platform/control integration, not an invented identity in the AI payload.

## 5. Gates and completion

Migrations 001–011 were already applied in the last verified staging state: never edit/reapply them.
Commit/review exact new migration SQL before approved hosted execution. Verify current hosted state
before changes; do not assume the October record is live verification today.

Provider/model, processing location, retention, budget/capacity and hosting/access still require
owner approval. Both mock and Gemini remain blocked in staging/production until approved integration.
Minimized text/bounded images and serving India/EU do not themselves authorize live or cross-border calls.
Complete local tests first; coordinate provider activation with Ruthvik, not a parallel bypass.

Done: reviewed migrations/worker plus synthetic integration under non-owner RLS, tested retries,
dependency/privacy/cleanup proofs, deployment/rollback notes and PRs ready for Ruthvik's verification.
Every PR lists exact commands/pass/fail/skip counts, blockers and hosted changes (or none);
require green CI on its exact commit and update HANDOVER. P2 dependency changes require both lockfiles.

Never expose/commit secrets, share `.env`, weaken MFA/RLS/scanning, delete volumes/buckets to
clear errors, force-push shared history or add AI attribution trailers. Use synthetic data only.
Do not edit `person4_tally/`. Ryan's short owner checklist is separate from your engineering work.
