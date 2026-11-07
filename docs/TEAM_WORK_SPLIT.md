# FinLedger — Current work by owner

Updated 2026-10-22. There are exactly three active assignment files. Keep each person's
current work in their existing file; replace superseded instructions instead of adding
another brief. Preserve useful verification evidence and completed milestones.

| Owner | Work file | Responsibility |
|---|---|---|
| Ryan | [RYAN_WORK.md](assignments/RYAN_WORK.md) | Approvals, private access and short final acceptance |
| Harshith | [HARSHITH_WORK.md](assignments/HARSHITH_WORK.md) | Database migration, extraction consumer, backend integration and operations/deployment |
| Ruthvik | [RUTHVIK_WORK.md](assignments/RUTHVIK_WORK.md) | AI/accounting/runtime wiring, controls/UI and detailed integration/Auth/release QA |

## How to work together

1. Read root [HANDOVER](../HANDOVER.md) completely for mandatory rules and current hosted-state evidence.
2. Work on your own branch; agree shared contracts and PR dependencies before editing another owner's area.
3. Harshith supplies migration/consumer/runtime pieces; Ruthvik integrates AI adapters and verifies workflows.
   Ryan does not repeat engineering tests or take back detailed QA.
4. Every PR records exact commands/results, blockers and hosted changes (or none). Require green CI on
   the exact integrated commit. Never claim live completion from mock/fake tests.
5. Update your existing work file and HANDOVER when status changes. Retire obsolete tasks from the
   active list; keep concise completed evidence, with dates/commit references, rather than duplicate lists.

## Shared references, not additional assignments

- [README](../README.md): prerequisites and local verification.
- [Deployment runbook](DEPLOYMENT_RUNBOOK.md): dependency proofs, approved deployment and rollback.
- [Auth design](plans/AUTH_SUPABASE_DESIGN.md): staff sign-in and session controls.
- [Durable AI schema proposal](proposals/AI_DURABLE_RETRIEVAL_USAGE_SCHEMA.md): reviewed implementation input.
- [Provider proposal](decisions/ADR-001-AI_PROVIDER.md): proposed policy, not activation permission.

Synthetic data only. Keep secrets private; no MFA/RLS/scanner bypass, force-push cleanup,
or paid/hosted activation without the appropriate approval. Harshith owns `person4_tally/`;
coordinate Tally changes with him.
Superseded assignment files were removed from the current tree; Git history preserves them.
