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
