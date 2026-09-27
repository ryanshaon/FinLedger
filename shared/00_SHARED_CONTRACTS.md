# FinLedger AP — Shared contracts

Source of truth: Full System Architecture, 23 Sep 2026.
Hard rule: extraction never posts. Only an approved or policy-cleared `VoucherDraft` hits an ERP.

These objects are frozen in week 0. Person 1 owns extract/map schemas. Person 2 owns persistence. Person 3 owns state + review. Person 4 owns ERP translation. No ERP field names leak into extract or RAG.

## CanonicalInvoice

```json
{
  "document_id": "uuid",
  "document_type": "purchase_invoice | debit_note | credit_note | expense | unknown",
  "vendor": { "name": "", "gstin": "", "pan": "", "state": "" },
  "buyer_gstin": "",
  "invoice_no": "",
  "invoice_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD | null",
  "irn": "string | null",
  "po_number": "string | null",
  "grn_number": "string | null",
  "taxable": 0,
  "cgst": 0, "sgst": 0, "igst": 0, "cess": 0, "round_off": 0, "total": 0,
  "line_items": [
    { "desc": "", "hsn_sac": "", "qty": 0, "rate": 0, "taxable": 0, "tax_rate": 0, "tax_amount": 0 }
  ],
  "bank_upi": "string | null",
  "confidences": { "invoice_no": 0.0, "date": 0.0, "gstin": 0.0, "total": 0.0 },
  "raw_markdown_path": "",
  "page_image_paths": []
}
```

## RiskScore

```json
{
  "document_id": "uuid",
  "score": 41,
  "band": "low | medium | high",
  "marks": [
    { "field": "invoice_no", "check": "confidence", "ok": true, "note": "0.97" },
    { "field": "grand_total", "check": "lines_mismatch", "ok": false, "note": "header 118000 lines 117400" }
  ]
}
```

Bands: Low 0–24 (auto-map; auto-post only if policy). Medium 25–59 (review before post). High 60–100 (stop, no post).

## VoucherDraft

```json
{
  "document_id": "uuid",
  "date": "YYYY-MM-DD",
  "voucher_type": "purchase | debit_note | credit_note | payment",
  "party_ledger": "",
  "party_create_proposal": false,
  "lines": [{ "ledger": "", "amount": 0, "is_debit": true, "cost_centre": null }],
  "gst": { "treatment": "intra | inter | exempt", "input_ledgers": [], "tax_breakup": {} },
  "tds": { "applicable": false, "section": null, "ledger": null, "amount": 0 },
  "bill_wise": { "ref_type": "new_ref", "ref": "INV-001", "due_date": "YYYY-MM-DD" },
  "narration": "",
  "attachment_document_id": "uuid",
  "reasons": ["mapped HSN 7208 to Purchase-RM from vendor memory"],
  "map_trace_id": "uuid"
}
```

## Other shared rows

| Object | Owner | Notes |
|---|---|---|
| Client | P2 + P3 | policy, erp_type, GSTINs, roles, auto-post cap |
| Vendor | P2 persist, P1 resolve | GSTIN is the real key |
| Document | P2 | raw file, channel, hash |
| ReviewTask | P3 | PDF + marks + draft |
| CorrectionEvent | P3 writes, P1 indexes | trains mapping memory |
| PostingJob | P4 | adapter call, erp_id, idempotency key |
| ErpLink / MappingTable | P4 | GSTIN↔Tally ledger, ledger↔G/L, tax↔tax code |

## Architecture refusals (all four)

- One global RAG across clients
- Auto-create vendors in Tally/SAP/Oracle without review
- LLM-written Tally XML or SAP payloads
- Post before the risk gate
- Mix client files or embeddings
- Treat GST portal / IMS as the only inbox
- Retry posts in a way that can duplicate invoices
- GST return filing in the same v1 loop
