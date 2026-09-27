# FinLedger — Product

**What it is.** A per-client Accounts Payable machine for Indian CAs and SMBs. Vendors send bills to one official link or one official email. FinLedger extracts the invoice, scores risk, maps ledgers with client-scoped RAG, waits for a human when needed, then posts an approved voucher into that client’s ERP. Tally Prime first. Zoho / QBO / SAP / Oracle later.

**Who it is for.** CA firms and SMBs running India GST / TDS / MSME payables. Not a bank. Not a GST return filer. Not a corporate-card issuer.

**One-line path.**

```
Vendor sends → extract + score + map → human reviews if risky → post to client ERP → payable stays open → payment closes it
```

**Hard rule.** Extraction never posts. Only an approved or policy-cleared `VoucherDraft` hits an ERP. The mapping LLM never emits Tally XML or SAP OData.

**In v1.** Purchase invoices, debit notes, credit notes, expense bills. GST validation on the invoice (GSTIN, tax math, HSN, intra/inter). TDS flag. MSME 45-day clock. Rule 37 180-day ITC warning. Tally post with bill-wise refs.

**Out of v1.** GSTR-1 / 3B filing, GSP / IRP, e-way create, corporate cards, employee T&E, silent vendor auto-create in SAP/Oracle.

## What the product does for a CA

1. Onboard a client in one sitting: pick ERP (Tally first), connect or upload ledgers, set policy, publish a unique link + inbound email.
2. Tell vendors once: send only here.
3. Open a queue. Every bill is New → Needs review → Approved → Posted → Exception.
4. Approve a draft. A purchase voucher appears in Tally with an open bill that ages.
5. Reviewer corrections teach the next bill for that vendor.

If that loop is not tight, do not add WhatsApp, payment runs, or a second ERP.

## How the whole thing works

Seven layers. Each layer has one job.

| Layer | Job | Owner |
|---|---|---|
| 1 Channels | Unique link, inbound email, CSV. Later WhatsApp. | Person 2 |
| 2 Ingest | Auth, MIME, virus/hash, object store, documents queue | Person 2 |
| 3 Extract | MarkItDown + OCR → `CanonicalInvoice` | P2 files, P1 LLM |
| 4 Control | Risk marks + band. Duplicate, GSTIN, tax math, MSME, 180-day | P3 code + P1 LLM slice |
| 5 AP brain | Vendor resolve, RAG + hard rules → `VoucherDraft` | Person 1 |
| 6 Workflow | State machine, approvals, SoD, exceptions | Person 3 |
| 7 Connectors | Translate draft → Tally / later other ERPs. Idempotent post | Person 4 |

Actors: vendors send bills. Client staff / CA review, approve, release pay. ERPs are the system of record. FinLedger is the control plane between intake and the ERP.

## India-specific product surface

- GSTIN is the vendor key (not W-9 / 1099).
- Tax math is CGST+SGST vs IGST vs place of supply, plus HSN.
- TDS sections instead of 1099.
- MSME 45-day pay clock.
- Rule 37: invoice older than 180 days puts ITC at risk.
- Payment rails later: NEFT / RTGS / IMPS / UPI / cheque — not ACH / virtual-card cashback.
- Approvals on web / email / later WhatsApp — not Slack.

## What “done” looks like

A CA connects Tally, gets a link and email, vendors send once, reviewer approves, purchase vouchers exist in Tally with open bills that age correctly.
