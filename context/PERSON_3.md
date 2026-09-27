# Person 3 — live context

Seat: control plane + review UI. Last updated: 2026-09-28

## Owns
Deterministic risk, band merge, state machine, 3-column review UI, SoD, CorrectionEvent writes.

## States
`received → extracted → scored → vendor_resolved → matched → coded → tax_checked → in_review → approved → scheduled → paid? → posted → reconciled → closed` plus `exception | rejected | resubmit`.

## Bands
Low 0–24 / Medium 25–59 / High 60–100.

## Policy flags
PO required, auto-post cap, maker-checker, TDS/MSME, 180-day, match mode invoice-only.

## Open questions
- Review UI stack?
- Who hosts signed PDF URLs from P2?
