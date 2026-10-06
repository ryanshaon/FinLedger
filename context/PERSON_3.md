# Person 3 — live context

Seat: control plane + review UI. Last updated: 2026-10-05

## Owns
Deterministic risk, band merge, state machine, 3-column review UI, SoD, CorrectionEvent writes.

## States
`received → extracted → scored → vendor_resolved → matched → coded → tax_checked → in_review → approved → scheduled → paid? → posted → reconciled → closed` plus `exception | rejected | resubmit`.

## Bands
Low 0–24 / Medium 25–59 / High 60–100.

## Policy flags
PO required, auto-post cap, maker-checker, TDS/MSME, 180-day, match mode invoice-only.

## Current status
- Implemented in `person3_control_ui/`.
- Deterministic risk, state transitions, review routing, SoD, corrections, exceptions, queue filters and three-column UI are live.
- Persistence is migration `person2_platform/.../004_control_review.sql`, under Person 2 RLS.
- Person 4 receives only idempotent `post` jobs created after approval or valid policy-clear.

## Open questions
- Production reverse proxy / SSO choice for injecting staff bearer authentication into browser page and form requests.

## Runtime facts for others
- P1 calls `ControlService.score(...)` with `CanonicalInvoice`, LLM score and field-level marks.
- P1 calls `ControlService.submit_draft(...)` after mapping.
- P4 claims `post` jobs: `{document_id, client_id, revision, idempotency_key, voucher_draft}`.
- P4 reports adapter failure through `erp_result(ok=false)`, which moves the bill to `exception` without another post job.
- P1 reads client-scoped `correction_events` for correction memory.
