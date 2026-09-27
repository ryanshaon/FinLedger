# Person 4 — Tally connector (first ERP)

FinLedger AP · India · Tally-first · September 2026  
Seat: local agent on :9000, masters sync, GSTIN↔ledger mapping table, idempotent post, attach PDF, outstanding pull.

## Product (read this first)

FinLedger is a per-client Accounts Payable machine for Indian CAs and SMBs. Vendors send bills to one official link or one official email. The platform extracts the invoice, scores risk, maps ledgers with client-scoped RAG, waits for a human when the bill is risky, then posts an approved `VoucherDraft` into that client's ERP. Tally Prime first.

**What it does.** Takes unstructured vendor PDFs/photos and turns them into posted purchase vouchers with open bills that age. GST / TDS / MSME native. Not a GST filer, not a bank, not a card issuer.

**How a bill moves.** Vendor sends → Person 2 stores markdown/images → Person 1 extracts `CanonicalInvoice` and maps a `VoucherDraft` via RAG → Person 3 scores and reviews → Person 4 posts to Tally. Extraction never posts. The mapping LLM never emits Tally XML.

**Full story.** `docs/PRODUCT.md` and `docs/HOW_IT_WORKS.md`.


## Mission

Translate an approved `VoucherDraft` into Tally Prime XML. Nothing else writes to Tally. Do not start Zoho / QBO / SAP until this loop is boring.

One-line path Person 4 owns:

```
approved VoucherDraft  →  validate masters  →  XML voucher + bill-wise  →  erp_id  →  attach PDF  →  outstanding
```

## Layers owned

| # | Layer | Person 4 piece |
|---|---|---|
| 7 | Connectors | Router + Tally adapter first. Shared contract listed below. |
| 14.1 | Tally Prime | Desktop / on-prem agent → XML HTTP :9000 |
| 14.5 | Master-data mapping | GSTIN → Tally ledger name. Purchase ledger → Tally name. Tax → input CGST/SGST/IGST. |
| 14.7 | Post flow | Idempotent on `document_id`. Errors → Exception. No double post. |

Not owned: RAG contents, intake doors, review UI, prompts.

Later adapters (do not staff now): Zoho Books, QBO, S/4 OData, B1 Service Layer, ECC BAPI, Fusion REST, EBS open interface.

## Shared connector contract

Every adapter, including Tally, implements:

```
connect()
fetch_masters()
post_invoice(draft)
post_credit_debit(draft)
fetch_open_bills()
post_payment(draft)   # optional, after invoice post is stable
health()
```

Posts are idempotent on `document_id`. Store `erp_id` + raw response on `PostingJob`.

## Tally runtime

```
API / post worker
        |
  Person 4 site agent on client PC
        |
  localhost:9000  Tally Prime XML
        |
  company currently loaded in Tally
```

The ERP is not exposed to the public internet. Same agent class will later cover on-prem SAP/EBS.

Offline path: if Tally is not running, download the XML for the CA to import. Bill stays `approved` / `post_pending`, never silently dropped.

## Mapping table

RAG maps to our ledgers first (Person 1). Person 4 then translates:

| Our key | Tally |
|---|---|
| Vendor GSTIN | Ledger name (party) |
| Purchase / expense ledger | Ledger name |
| GST 18% intra | Input CGST + Input SGST |
| GST inter | Input IGST |
| TDS 194J | TDS ledger |
| Bill-wise | New Ref = invoice_no, due date |

If the Tally vendor ledger does not exist: set create-proposal. Reviewer must approve. Never auto-create in MVP.

## Post flow

```
approved VoucherDraft
  -> router picks adapter from client.erp_type
  -> adapter.validate(draft, masters)
       # company loaded, ledgers exist, period open-ish, tax ledgers present
  -> adapter.post()
       Tally: XML purchase / DN / CN + bill-wise
  -> store erp_id
  -> attach source PDF (where Tally allows; else keep link in narration / extra file)
  -> fetch open items for ageing
  -> ERP errors -> exception
     NO retry that can double-post
```

Voucher types: purchase invoice, debit note, credit note, later payment with bill-wise refs.

## Agent responsibilities

- Talk only to the loaded company.
- Health: Tally up, port 9000, company name.
- `fetch_masters`: company name, ledgers, groups, outstanding payables.
- Write masters snapshot to Person 2 tables so Person 1 can embed under `client_id`.
- Import purchase / DN / CN / payment.
- Create ledger only after reviewer approved “new vendor.”
- Idempotency: if `PostingJob` already has `erp_id` for this `document_id`, return that id. Do not import again.

## XML rules

- Person 1 / Person 3 never generate XML.
- Templates live in the adapter. Draft fields fill slots.
- Bill-wise New Ref must equal invoice_no from the draft.
- Narration from draft, plus `finledger:document_id=` for support.
- Amounts from draft only. Do not re-compute GST in the adapter except to reject mismatch.

## Interfaces

**From Person 3**

- approved `VoucherDraft`
- idempotency key
- attachment `document_id`

**From Person 2**

- encrypted `erp_link` row (agent pairing token, company name)
- object store for XML artifacts / request logs keyed by `client_id`
- `mapping_table` persistence

**To Person 2 / Person 1**

- masters snapshot after connect and on a schedule
- posted bill pointer so RAG can index last N invoices

**To Person 3**

- `PostingJob` success (`erp_id`) or exception (Tally error text, no retry storm)

## Week plan (Person 4)

| Week | Ship |
|---|---|
| 0 | Agent hello + company read + health |
| 1 | `fetch_masters` → empty mapping table |
| 2 | XML preview of draft (no post) |
| 3 | Post on approve + `erp_id` + attach / store PDF |
| 4 | Idempotent post, DN/CN, outstanding sync |

Do not start Zoho or SAP in these four weeks.

## Acceptance tests

- Health fails cleanly when Tally is closed.
- `fetch_masters` returns ledgers for the loaded company only.
- Preview XML contains party, purchase, GST lines, bill-wise ref = invoice_no.
- Approve twice → one Tally voucher.
- Missing party ledger → reject to Exception with create-proposal, no silent create.
- Tally rejection text lands on the ReviewTask exception tray.
- Masters snapshot is client-scoped.
- Offline XML file is produced when port 9000 is down.

## Refusals for this seat

- LLM-written XML
- Auto-create vendor / ledger
- Fire-and-forget import
- Retry that can duplicate
- Exposing Tally to the public internet
- Building S/4 / Fusion adapters before the Tally loop is reliable
- Pretending ECC speaks S/4 OData when that work starts later
