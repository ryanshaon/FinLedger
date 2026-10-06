# Handoffs

Producer writes the payload contract when it changes.

| When | From → To | Payload |
|---|---|---|
| File ready to extract | P2 → P1 | document_id, client_id, raw_markdown, layout_blocks, page_image_paths, source_channel |
| Fields extracted | P1 → P3 | CanonicalInvoice + confidences |
| Risk ready | P1 + P3 → SM | Merged RiskScore |
| Draft ready | P1 → P3 + P4 | VoucherDraft + reasons + MapTrace |
| Human said yes | P3 → P4 | approved VoucherDraft + idempotency key |
| Posted or failed | P4 → P3 + P1 | erp_id or exception |
| Reviewer fixed a ledger | P3 → P1 | CorrectionEvent |

### 2026-09-28 — P2 → P1 `extract` job payload (live)

`jobs.payload` on queue `extract`. Paths are object-store keys under `client_id/yyyy/mm/doc_id/`.

```json
{
  "document_id": "uuid", "client_id": "uuid",
  "source_channel": "link | email | csv",
  "document_class": "invoice | statement | csv_row",
  "mime": "application/pdf", "content_hash": "sha256 hex",
  "raw_path": "…/raw.pdf",
  "raw_markdown_path": "…/markdown.md | null (scan/photo)",
  "layout_blocks_path": "…/layout.json | null (photo, xlsx, csv)",
  "page_image_paths": ["…/pages/p001.png"],
  "page_count": 1, "is_scanned": false, "phish_flag": false, "po_number": "string | null"
}
```

`layout.json` = `{"pages": [{page, width, height, has_text}], "blocks": [{page, type: "line", bbox: [x0, top, x1, bottom], text} | {page, type: "table", bbox, cells: [[...]]}], "truncated": bool}` (PDF points, top-left origin). Page images exist only for pages without a text layer (200 DPI PNG). `finledger_platform.worker.load_extract_input()` resolves the paths to text/dict. Statements are enqueued with `document_class=statement` — P1 decides whether to extract them.

### 2026-10-05 — P3 → P4 approved post job (live)

`jobs.payload` on queue `post`. Created only after human approval or valid Low-risk policy clear.

```json
{
  "document_id": "uuid", "client_id": "uuid", "revision": 3,
  "idempotency_key": "document_id:r3",
  "voucher_draft": { "...": "frozen VoucherDraft contract" }
}
```

P4 uses `idempotency_key` for adapter idempotency. Adapter errors return through `ControlService.erp_result(ok=false, error=...)`; P3 marks `exception` and does not create a retry storm.

### 2026-10-05 — P3 → P1 correction memory (live)

Every edit appends `correction_events`: `client_id, document_id, vendor_gstin, field, old_value, new_value, hsn, description, actor_id, created_at`. P1 indexes them only inside that client namespace.
