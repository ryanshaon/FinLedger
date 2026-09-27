# Person 3 — Control plane + Review UI

FinLedger AP · India · Tally-first · September 2026  
Seat: deterministic risk, band merge, state machine, three-column review UI, approvals / SoD, exceptions, CorrectionEvent.

## Product (read this first)

FinLedger is a per-client Accounts Payable machine for Indian CAs and SMBs. Vendors send bills to one official link or one official email. The platform extracts the invoice, scores risk, maps ledgers with client-scoped RAG, waits for a human when the bill is risky, then posts an approved `VoucherDraft` into that client's ERP. Tally Prime first.

**What it does.** Takes unstructured vendor PDFs/photos and turns them into posted purchase vouchers with open bills that age. GST / TDS / MSME native. Not a GST filer, not a bank, not a card issuer.

**How a bill moves.** Vendor sends → Person 2 stores markdown/images → Person 1 extracts `CanonicalInvoice` and maps a `VoucherDraft` via RAG → Person 3 scores and reviews → Person 4 posts to Tally. Extraction never posts. The mapping LLM never emits Tally XML.

**Full story.** `docs/PRODUCT.md` and `docs/HOW_IT_WORKS.md`.


## Mission

Own transitions. The LLM fills slots. Code decides whether a bill may move. Humans sit between Map and Post whenever risk says so.

One-line path Person 3 owns:

```
CanonicalInvoice + marks  →  band  →  in_review or auto-clear  →  approved VoucherDraft  →  Person 4
```

## Layers owned

| # | Layer | Person 3 piece |
|---|---|---|
| 4 | Control | Deterministic checks + merge with Person 1 LLM marks. Field-level review marks. |
| 6 | Workflow | State machine, approval policy, payer role, recurring/prepaid later, exceptions. |
| 12 | Review UI | Three columns, actions, AI paragraph display (text from Person 1). |
| 9–10 | Approve / schedule | Routed review. Payment run is phase 9 — schedule/due date only in MVP. |

Not owned: prompts, object store internals, Tally XML.

## State machine (code owns this)

```
received → extracted → scored → vendor_resolved
       → matched → coded → tax_checked
       → in_review → approved
       → scheduled → paid (optional)
       → posted → reconciled → closed

exception / rejected / resubmit can fire from any control step
```

MVP match mode: invoice-only + optional PO number. 2-way / 3-way later.

LLM does not skip `in_review` because it is confident. Band + client policy decide.

## Risk merge

Person 1 may add language/fraud hints. Person 3 always runs code:

| Family | Checks |
|---|---|
| Extraction | Confidence on invoice_no, date, GSTIN, total. Line-item sum ≠ header. Unreadable page. |
| Identity / fraud | GSTIN checksum or name mismatch. Buyer GSTIN ≠ this client. New vendor. Bank changed vs last bill. Sender domain ≠ vendor domain. Duplicate: vendor + invoice_no + FY (+ amount). |
| Tax / India | CGST+SGST vs IGST vs place of supply. Unusual rate. IRN missing for obvious e-invoice class (soft). TDS likely but not computed. MSME + due > 45 days. Invoice older than 180 days (ITC reversal). |
| Match / process | PO required by policy but missing. Qty/rate vs PO. GRN missing for goods. Amount spike vs vendor median. Future-dated invoice. |

Bands:

| Band | Range | Action |
|---|---|---|
| Low | 0–24 | Auto-map allowed. Auto-post only if client policy says so. |
| Medium | 25–59 | Map, but must review before post. |
| High | 60–100 | Stop. Review required. No post. |

Marks are field-level, not one comment.

Example: `invoice_no conf 0.97 ok · gstin checksum_ok · grand_total lines_mismatch · vendor fuzzy NEW_VENDOR · duplicate none · Non-PO · risk 41 MEDIUM → review`

## Separation of duties

- Extractor/mapper ≠ poster for the same user above threshold.
- Bill approver ≠ payment releaser.
- No self-approve.
- Re-approve only if vendor, amount, or bank details change after first approval.
- Roles on Client: AP clerk, approver, payer.

## Review UI

One screen, three columns:

1. Source document (PDF / page images from Person 2)
2. Extracted fields with confidence and marks (editable)
3. Proposed voucher — ledgers, GST, TDS, bill ref — plus risk band and Person 1 AI paragraph

Queue views: New → Needs review → Approved → Posted → Exception

Actions: Approve & post · Approve draft only · Edit · Reject · Ask vendor to resend

High-risk or over-limit goes to checker.

Every edit writes `CorrectionEvent`. That is how Person 1 mapping becomes accurate per client in two to three weeks.

## Client policy flags Person 3 defines

Person 2 stores them. Person 1 reads them. Person 3 enforces them.

- PO required (global or above amount)
- Auto-post cap
- Maker-checker
- TDS sections to flag
- MSME 45-day clock on
- 180-day ITC warning on
- Match mode: invoice-only | 2-way | 3-way

## Recurring / close (after Tally loop)

- Recurring bills (rent, retainership, SaaS) auto-draft each period
- Prepaid / AMC / insurance amortization journals
- Month-end accrue unprocessed bills still in queue when policy requires
- Ageing, MSME overdue, ITC-at-risk views — need Person 4 outstanding pull

MVP can stop at schedule (due date, MSME pay-by, suggested batch). Full payment run is phase 9.

## Interfaces

**From Person 1**

- `CanonicalInvoice`, LLM marks, `VoucherDraft`, AI paragraph, `MapTrace`

**From Person 2**

- documents + signed PDF URLs + client + policy columns + roles

**To Person 1**

- `CorrectionEvent` `{ client_id, vendor_gstin, field, old, new, hsn, desc, actor, at }`

**To Person 4**

- `approved VoucherDraft` + `idempotency_key` = `document_id` (or document_id + revision)
- Never send a draft that is not `approved` or policy-cleared Low with auto-post on

**From Person 4**

- `erp_id` or exception. Failures return to Exception. No silent drop.

## Week plan (Person 3)

| Week | Ship |
|---|---|
| 0 | State enum, review shell with PDF only |
| 1 | Deterministic risk + field marks |
| 2 | Three-column UI + edit → CorrectionEvent |
| 3 | Approve draft only + SoD + exception tray |
| 4 | Queue filters + MSME / 180-day clocks |

## Acceptance tests

- High band cannot reach Person 4.
- Medium always creates a ReviewTask.
- Low + auto-post cap exceeded → review, not post.
- Duplicate vendor+invoice_no+FY marks and holds.
- Buyer GSTIN ≠ client GSTIN marks High.
- Edit of party ledger writes CorrectionEvent.
- Approver cannot also release pay on the same user.
- Reject sets resubmit / rejected and never posts.
- Person 4 error moves the bill to Exception, not retry-storm.

## Refusals for this seat

- Letting the mapper own transitions
- Single blob comment instead of field-level marks
- Self-approve
- Posting from the UI without going through the state machine
- Building Slack-style chat as the approval surface (WhatsApp/email later, after Tally posts)
