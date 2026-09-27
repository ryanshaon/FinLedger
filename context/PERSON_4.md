# Person 4 — live context

Seat: Tally connector. Last updated: 2026-09-28

## Owns
Local agent `:9000`, fetch_masters, mapping table, idempotent post, attach PDF, outstanding pull.

## Current status
Tally first. Zoho / QBO / SAP parked until Tally loop is boring.

## Facts
- Adapter contract: connect, fetch_masters, post_invoice, post_credit_debit, fetch_open_bills, post_payment?, health
- Idempotency key = document_id
- New vendor = proposal only

## Open questions
- Agent install path on CA Windows box?
- How pairing token is stored (P2 encrypted row)?
