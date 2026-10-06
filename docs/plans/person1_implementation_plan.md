# Person 1 — Implementation Plan

> Owner: Person 1 (AI / RAG / Tokens)
> Created: 2026-09-28
> Status: IMPLEMENTED AS LOCAL PROTOTYPE — live providers and persistent RAG deferred

---

## 1. Product recap

FinLedger AP is a per-client Accounts Payable machine for Indian CAs and SMBs.
Vendors send bills to a unique link or inbound email. Person 2 ingests and
converts files to MarkItDown markdown + page images. **Person 1** then:

1. **Extracts** structured `CanonicalInvoice` from that markdown (vision only if
   digital text is empty).
2. Optionally adds narrow **LLM risk marks** (fraud / language hints) — Person 3
   merges the full score.
3. **Maps** the invoice to an ERP-agnostic `VoucherDraft` using client-scoped RAG
   (ledgers, vendor master, posted bills, correction memory, policy, hard rules).
4. Generates a 4–6 line **AI approval paragraph** for the review UI.
5. Learns from every **CorrectionEvent** the reviewer creates.
6. Logs every model call (tokens, cost, latency) and enforces a per-bill kill
   switch.

Extraction never posts. The mapping LLM never emits Tally XML, Zoho JSON, or
SAP OData. RAG namespace is always `client_id`. There is no global ledger brain.

---

## 2. Target repo layout

```
packages/
  contracts/
    schemas/
      canonical_invoice.py      # Pydantic model
      risk_score.py             # LLM risk marks only (Person 3 owns full score)
      voucher_draft.py          # ERP-agnostic draft
      correction_event.py       # From Person 3 → Person 1 memory
      map_trace.py              # Retrieved chunks, model, tokens, cost
    golden/
      intra_gst_18.json         # CGST+SGST 9%+9% purchase invoice
      igst_import.json          # IGST inter-state invoice
      professional_194j.json    # Professional fee that triggers 194J TDS
    json_schemas/               # Auto-exported JSON Schema files
    __init__.py
    conftest.py

  extract/
    extract_worker.py           # Markdown → CanonicalInvoice
    vision_fallback.py          # Page images → CanonicalInvoice (scan path)
    prompts/
      extract_digital.txt
      extract_vision.txt
    tests/
      test_extract_digital.py
      test_extract_vision.py
      fixtures/                 # 20–50 Indian bill markdowns + expected JSONs

  rag/
    indexer.py                  # Upsert chunks into client namespace
    retriever.py                # Query by GSTIN+name+desc+HSN+amount+doctype
    chunk_types.py              # ledger|vendor|posted_bill|memory|policy|hard_rule
    isolation.py                # Namespace enforcement
    tests/
      test_isolation.py         # Client A cannot retrieve Client B
      test_retrieval.py

  mapper/
    map_worker.py               # Invoice + chunks + policy → VoucherDraft
    prompts/
      map_prompt.txt
    tests/
      test_map_worker.py
      test_create_proposal.py

  memory/
    memory_indexer.py           # CorrectionEvent → memory chunk upsert
    tests/
      test_memory_reuse.py      # HSN remap appears on next bill

  risk_llm/
    risk_llm_worker.py          # Narrow LLM marks (fraud/language)
    tests/
      test_risk_marks.py

  summary/
    summary_writer.py           # 4–6 line AI approval paragraph
    tests/
      test_summary.py

  usage/
    model_router.py             # Route to extract/map/summary models
    usage_writer.py             # Log every call
    kill_switch.py              # Per-bill token cap enforcement
    tests/
      test_usage_log.py
      test_kill_switch.py

  eval/
    harness.py                  # Fixture runner
    metrics.py                  # Field F1, ledger accuracy, tokens, cost
    dashboard.py                # Cost-per-bill summary
    fixtures/                   # 30–50 Indian bills with ground truth
    tests/
      test_harness.py
```

---

## 3. Milestones

### M0 — Foundations (Week 0)

| Deliverable | Detail |
|---|---|
| Pydantic contracts | `CanonicalInvoice`, `RiskScore` (LLM marks), `VoucherDraft`, `CorrectionEvent`, `MapTrace` |
| Golden JSON fixtures | 3 files: intra-GST 18%, IGST, professional-fee 194J |
| JSON Schema export | Auto-generate from Pydantic for cross-language use |
| Model router skeleton | Dispatch by purpose (`extract`, `extract_vision`, `risk_llm`, `map`, `summary`) |
| Usage table + writer | `client_id`, `doc_id`, model, tokens_in, tokens_out, latency_ms, cost_usd, purpose |
| Per-bill kill switch | Abort if cumulative tokens for a `doc_id` exceed cap |
| 20-bill fixture seed | MarkItDown markdown samples + expected `CanonicalInvoice` JSONs |

**Exit criteria:** `pytest packages/contracts packages/usage` green. Golden JSONs
validate against schemas. Router resolves model names. Kill switch fires on synthetic
over-budget call.

### M1 — Digital extract (Week 1)

| Deliverable | Detail |
|---|---|
| `extract_worker` | Markdown → `CanonicalInvoice`. Schema-constrained JSON output. |
| Line-sum mismatch handling | If `sum(line_items.taxable)` ≠ `header.taxable`, set a confidence mark. Do not invent or rewrite the total. |
| Confidence scores | `invoice_no`, `date`, `gstin`, `total` — each 0.0–1.0 |
| Usage integration | Every extract call logged via `usage_writer` |
| Tests on 5+ digital invoices | Field presence, GSTIN format, total consistency |

**Exit criteria:** Extract returns valid `CanonicalInvoice` on ≥ 5 digital GST
invoices. Usage row exists for every call. Mismatch is marked, never papered over.

### M2 — Client RAG + mapper (Week 2)

| Deliverable | Detail |
|---|---|
| Client-scoped indexer | Upsert chunks: `ledger`, `vendor`, `posted_bill`, `memory`, `policy`, `hard_rule`. Namespace = `client_id`. |
| Client isolation test | Client A chunks are invisible to Client B queries. |
| Retriever | Query = GSTIN + name + line desc + HSN + amount band + doc_type. Return top-K chunks. |
| `map_worker` | Invoice + retrieved chunks + policy → `VoucherDraft` + reasons + `MapTrace`. |
| Vendor create-proposal | Unknown vendor → `party_create_proposal = true`. Never auto-create. |
| Bill-wise refs | `ref_type = new_ref`, `ref = invoice_no`, `due_date = invoice_date + credit_days`. |

**Exit criteria:** Mapper output validates against `VoucherDraft` schema. Zero XML
tags in output. Isolation test passes. `party_create_proposal` set for unknown vendor.
MapTrace records retrieved chunks.

### M3 — Vision + summary + eval (Week 3)

| Deliverable | Detail |
|---|---|
| Vision extract path | Page images + OCR text → `CanonicalInvoice`. Only activated when digital text is empty. |
| Risk LLM marks | Narrow fraud/language hints. No GSTIN checksum, no tax math, no duplicate detection (Person 3). |
| AI approval paragraph | 4–6 line summary: vendor, amount, GST treatment, risk band, why mapped this way. |
| Eval harness | Fixture runner on 30–50 bills. Metrics: field F1, ledger accuracy, tokens/bill, cost/bill, review-escape rate. |
| Cost dashboard | Summary view: avg/p95 tokens per bill, cost breakdown by model call purpose. |

**Exit criteria:** Vision path returns valid invoice on scan fixture. Summary is ≤ 6
lines and mentions key fields. Eval harness runs and reports metrics for all fixtures.

### M4 — Correction memory + tuning (Week 4)

| Deliverable | Detail |
|---|---|
| `memory_indexer` | Consumes `CorrectionEvent`. Upserts memory chunk keyed by vendor GSTIN + HSN + description. |
| Memory reuse test | Edit "HSN 9983 → Professional fees 194J" for vendor X. Next bill from vendor X auto-maps to Professional fees. |
| Posted-bill indexing | After Person 4 reports success, index the posted bill as a `posted_bill` chunk. |
| Prompt tuning | Adjust extract/map prompts based on eval harness + real corrections. |
| Full eval pass | Re-run harness. Report improvement from M3 baseline. |

**Exit criteria:** Correction memory test passes end-to-end. Eval metrics improve or
hold. Every model call still has a usage row. Kill switch still fires.

---

## 4. Interfaces

### Consumed from Person 2 (ingest → extract)

| Field | Type | Notes |
|---|---|---|
| `document_id` | UUID | Primary key for the document |
| `client_id` | UUID | Tenant namespace for RAG |
| `raw_markdown` | string/path | MarkItDown output for digital PDFs |
| `layout_blocks` | JSON array | Page, bbox, table cells |
| `page_image_paths` | string[] | For scan/photo path only |
| `source_channel` | enum | `link` / `email` / `csv` |

### Emitted to Person 3 (extract → score, map → review)

| Payload | When |
|---|---|
| `CanonicalInvoice` + confidences | After extract |
| LLM risk marks (subset of `RiskScore.marks`) | After optional risk LLM |
| `VoucherDraft` + reasons + `MapTrace` ID | After map |
| AI approval paragraph (string) | After summary |

### Consumed from Person 3 (review → memory)

| Payload | When |
|---|---|
| `CorrectionEvent` | Every reviewer edit (ledger remap, amount fix, etc.) |

### Consumed from Person 4 (post → memory)

| Payload | When |
|---|---|
| Masters (ledgers, vendors) | After Tally sync → Person 2 stores → Person 1 indexes |
| Posted bill confirmation (`doc_id`, `erp_id`) | After successful Tally post |

---

## 5. Model / token budget

| Call | Model tier | Estimated tokens (in+out) | Notes |
|---|---|---|---|
| `extract` | Cheap / fast | ~2,000–4,000 | Truncated markdown only |
| `extract_vision` | Multimodal | ~3,000–6,000 | Only for scans |
| `risk_llm` | Small | ~500–1,500 | Optional, skip if high confidence |
| `map` | Stronger | ~3,000–8,000 | Invoice + top-8 RAG chunks + policy |
| `summary` | Cheap | ~500–1,000 | 4–6 line output |

**Per-bill cap:** 20,000 tokens total across all calls. Kill switch aborts if exceeded.

**Cost target:** < $0.05 USD per bill at current Gemini/GPT pricing.

**Rules:**
- Prompt cache where the provider allows.
- If map model is down, leave bill in `extracted`/`scored` state. Never post.
- LLM never writes connector payloads.

---

## 6. Test matrix

| # | Test | Milestone | What it proves |
|---|---|---|---|
| 1 | Golden JSON validates against Pydantic schema | M0 | Contracts are correct |
| 2 | JSON Schema export matches Pydantic | M0 | Cross-language contracts work |
| 3 | Usage row written for every router call | M0 | Logging works |
| 4 | Kill switch aborts at token cap | M0 | Budget enforcement |
| 5 | Digital extract returns valid `CanonicalInvoice` | M1 | Core extraction works |
| 6 | Line-sum mismatch is marked, total unchanged | M1 | No inventing totals |
| 7 | Confidence fields present and in 0.0–1.0 | M1 | Extraction quality signal |
| 8 | Client A chunks invisible to Client B | M2 | RAG isolation |
| 9 | Mapper returns valid `VoucherDraft` | M2 | Mapping works |
| 10 | Output contains zero XML/HTML tags | M2 | No ERP leakage |
| 11 | Unknown vendor → `party_create_proposal=true` | M2 | No auto-create |
| 12 | Bill-wise ref = invoice_no | M2 | Tally bill tracking |
| 13 | Vision extract on scan fixture | M3 | Scan path works |
| 14 | Summary ≤ 6 lines | M3 | UI-ready paragraph |
| 15 | Eval harness runs and reports F1 + cost | M3 | Quality measurement |
| 16 | Correction memory changes next map output | M4 | Learning loop works |
| 17 | Posted-bill chunk indexed | M4 | Historical context grows |

---

## 7. Refusals (hard-coded for every subagent)

1. **No global RAG** across clients.
2. **No raw pixels into the mapping prompt** before MarkItDown/OCR text exists.
3. **No Tally XML**, SAP OData, Zoho JSON, QBO payloads from an LLM.
4. **No post** to any ERP.
5. **No auto-create** of vendor or ledger. Proposal flag only.
6. **No inventing** header totals to force balance. Mark the mismatch.
7. **No skipping review** because the model is confident. Band + policy belong to Person 3.
8. **No mixing** client files or embeddings.
9. **No scope creep** to UI, ingest doors, or connectors.
