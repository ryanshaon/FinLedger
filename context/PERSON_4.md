# Person 4 — live context

Seat: Tally connector. Last updated: 2026-10-06

## Owns
Local agent `:9000`, fetch_masters, mapping table, idempotent post, attach PDF, outstanding pull.

## Current status
**Architecture only.** No local agent, adapter, installer, posting service, or
connector tests exist yet. Tally remains first; Zoho, QBO and SAP are parked.

## Facts
- Adapter contract: connect, fetch_masters, post_invoice, post_credit_debit, fetch_open_bills, post_payment?, health
- Business idempotency key = `document_id`; attempt identity = `(document_id, revision)`
- New vendor = proposal only

## Open questions
- Agent install path on CA Windows box?
- How pairing token is stored (P2 encrypted row)?
