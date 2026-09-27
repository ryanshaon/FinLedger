# How FinLedger runs one bill

This is the shared story every person and every agent must keep in their head.

## 0. Client exists

CA creates a workspace. Picks `erp_type=tally`. Connects Tally or uploads ledgers. Sets policy (PO required, auto-post cap, maker-checker, TDS/MSME). Publishes:

- unique link `https://app…/i/{client_public_token}`
- inbound email `invoices.{slug}@inbound.domain`

Roles: AP clerk, approver, payer.

## 1. Vendor sends

Vendor uploads a PDF/photo on the link or emails one invoice. Optional PO number. System replies received or resubmit. Personal Gmail / personal WhatsApp is not a door.

## 2. Ingest (Person 2)

Auth token or SPF/DKIM. Virus scan. Hash. Split attachments. Store raw file at `client_id/yyyy/mm/doc_id/`. Write `documents` row. Digital PDF → MarkItDown markdown + layout blocks. Scan/photo → page images. Enqueue `extract`.

## 3. Extract (Person 1)

Do not send raw pixels to the mapping LLM first. Schema-constrained model reads markdown (and vision only if needed). Writes `CanonicalInvoice`: vendor GSTIN/name, buyer GSTIN, invoice no/date/IRN, lines + HSN, CGST/SGST/IGST, totals, confidences. If line sum ≠ header, mark it. Do not invent a total.

## 4. Score (Person 3 + Person 1 slice)

Code checks: GSTIN checksum, tax math, duplicate vendor+invoice+FY, buyer GSTIN, MSME 45, 180-day, PO missing, sender domain. Person 1 may add language/fraud hints. Merge to 0–100.

- Low 0–24: auto-map. Auto-post only if policy says so.
- Medium 25–59: map, review before post.
- High 60–100: stop. Review. No post.

## 5. Map (Person 1)

Client-scoped RAG only. Namespace = `client_id`. Retrieve ledgers, vendor master, last N posted bills for that vendor, mapping memory, cost centres, policy, hard rules. Query = GSTIN + name + line desc + HSN + amount band + doc type.

Output `VoucherDraft`: party ledger (propose create, never auto-create), purchase/expense split, GST input ledgers, TDS if policy hits, bill-wise New Ref = invoice_no, due date, narration, reasons. Store `MapTrace` (chunks, model, tokens, cost).

## 6. Review (Person 3)

Three columns: PDF | fields + marks | proposed voucher + risk band + AI paragraph. Actions: Approve & post, Approve draft only, Edit, Reject, Ask resend. Every edit writes `CorrectionEvent` so Person 1 memory learns.

## 7. Post (Person 4)

Only approved or policy-cleared drafts. Tally agent on localhost:9000. Validate masters. Write XML purchase + bill-wise. Store `erp_id`. Idempotent on `document_id`. Failure → Exception. No silent drop. No double post.

## 8. After post

Outstanding / ageing from Tally. Payment against bill-wise ref later. Reviewer corrections already in mapping memory for that vendor.

## What never happens

Global RAG. LLM XML. Post before the risk gate. Auto-create vendor. Mix client files. GST portal as the inbox. Retry that duplicates a voucher.
