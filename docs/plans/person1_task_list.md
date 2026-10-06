# Person 1 — Task List

> Each task = one Gemini Pro subagent job. One concern per task.
> Max 3 workers spawned concurrently. Merge before next wave.
> Created: 2026-09-28 | Status: IMPLEMENTED AS LOCAL PROTOTYPE — 2026-10-06

---

## Wave 1 — Foundations (M0)

### Task 1: Pydantic contracts + golden JSON
- **Agent:** `p1-contracts`
- **Reads:** `shared/00_SHARED_CONTRACTS.md`, `docs/HOW_IT_WORKS.md`, `context/PERSON_1.md`
- **Writes:**
  - `packages/contracts/schemas/canonical_invoice.py`
  - `packages/contracts/schemas/risk_score.py`
  - `packages/contracts/schemas/voucher_draft.py`
  - `packages/contracts/schemas/correction_event.py`
  - `packages/contracts/schemas/map_trace.py`
  - `packages/contracts/golden/intra_gst_18.json`
  - `packages/contracts/golden/igst_import.json`
  - `packages/contracts/golden/professional_194j.json`
  - `packages/contracts/__init__.py`
- **Tests:** Golden JSONs validate against Pydantic models. Round-trip serialize/deserialize.
- **Done when:** `pytest packages/contracts/` passes.

### Task 2: JSON Schema export
- **Agent:** `p1-contracts` (second pass) or inline
- **Reads:** Output of Task 1
- **Writes:** `packages/contracts/json_schemas/*.json` (one per schema)
- **Tests:** Each exported JSON Schema validates the corresponding golden fixture.
- **Done when:** JSON Schema files exist and validate.

### Task 3: Model router + usage writer
- **Agent:** `p1-usage` (custom or ad-hoc)
- **Reads:** `person1_ai_rag/ARCHITECTURE.md` (token architecture section), `context/PERSON_1.md`
- **Writes:**
  - `packages/usage/model_router.py`
  - `packages/usage/usage_writer.py`
  - `packages/usage/tests/test_usage_log.py`
- **Tests:** Router resolves model by purpose. Writer logs a row with all required fields (`client_id`, `doc_id`, model, tokens_in, tokens_out, latency_ms, cost_usd, purpose`).
- **Done when:** `pytest packages/usage/` passes.

### Task 4: Per-bill kill switch
- **Agent:** `p1-usage` (second concern, but small enough to combine with Task 3 if preferred)
- **Reads:** Output of Task 3
- **Writes:**
  - `packages/usage/kill_switch.py`
  - `packages/usage/tests/test_kill_switch.py`
- **Tests:** Synthetic call sequence exceeding 20K tokens triggers abort. Under-budget call completes.
- **Done when:** Kill switch test passes.

---

## Wave 2 — Digital extract (M1)

### Task 5: Extract worker (digital path)
- **Agent:** `p1-extract`
- **Reads:** `shared/00_SHARED_CONTRACTS.md`, `person1_ai_rag/ARCHITECTURE.md`, contracts from Task 1
- **Writes:**
  - `packages/extract/extract_worker.py`
  - `packages/extract/prompts/extract_digital.txt`
  - `packages/extract/tests/test_extract_digital.py`
  - `packages/extract/tests/fixtures/` (≥5 digital invoice markdowns + expected JSONs)
- **Tests:**
  - Returns valid `CanonicalInvoice` on each fixture.
  - GSTIN format matches `\d{2}[A-Z]{5}\d{4}[A-Z]{1}\d{1}[A-Z]{1}\d{1}`.
  - Line-sum mismatch → confidence mark set, total unchanged.
  - Confidence fields in 0.0–1.0.
  - Usage row written per call (wires into router from Task 3).
- **Done when:** `pytest packages/extract/` passes on ≥ 5 digital invoices.

### Task 6: Fixture seed (20 Indian bills)
- **Agent:** ad-hoc worker
- **Reads:** `shared/00_SHARED_CONTRACTS.md` for field reference
- **Writes:** `packages/extract/tests/fixtures/` — 20 markdown + expected JSON pairs covering:
  - Intra-state CGST+SGST (multiple rates: 5%, 12%, 18%, 28%)
  - Inter-state IGST
  - Debit note, credit note
  - Multi-line invoice
  - Professional fee (194J pattern)
  - Line-sum mismatch case
  - Missing fields (no PO, no IRN, no due_date)
- **Done when:** 20 fixture pairs exist. Each expected JSON validates against `CanonicalInvoice` schema.

---

## Wave 3 — Client-scoped RAG (M2)

### Task 7: Client-scoped indexer + chunk types
- **Agent:** `p1-retriever`
- **Reads:** `person1_ai_rag/ARCHITECTURE.md` RAG section, contracts
- **Writes:**
  - `packages/rag/indexer.py`
  - `packages/rag/chunk_types.py`
  - `packages/rag/isolation.py`
- **Tests:** Upsert chunks for two clients. Verify namespace prefixing.
- **Done when:** Chunks stored with `client_id` namespace. Types match: `ledger`, `vendor`, `posted_bill`, `memory`, `policy`, `hard_rule`.

### Task 8: Retriever + isolation test
- **Agent:** `p1-retriever` (second concern)
- **Reads:** Output of Task 7
- **Writes:**
  - `packages/rag/retriever.py`
  - `packages/rag/tests/test_isolation.py`
  - `packages/rag/tests/test_retrieval.py`
- **Tests:**
  - Query by GSTIN + name + desc + HSN + amount band + doc_type returns relevant chunks.
  - Client A chunks **never** appear in Client B queries (hard isolation test).
- **Done when:** `pytest packages/rag/` passes. Isolation test explicitly asserts zero leakage.

### Task 9: Map worker → VoucherDraft
- **Agent:** `p1-mapper`
- **Reads:** `shared/00_SHARED_CONTRACTS.md` VoucherDraft, contracts, retriever from Task 8
- **Writes:**
  - `packages/mapper/map_worker.py`
  - `packages/mapper/prompts/map_prompt.txt`
  - `packages/mapper/tests/test_map_worker.py`
  - `packages/mapper/tests/test_create_proposal.py`
- **Tests:**
  - Output validates against `VoucherDraft` schema.
  - Zero XML/HTML tags in any field.
  - Unknown vendor → `party_create_proposal = true`.
  - Bill-wise: `ref = invoice_no`, `due_date = invoice_date + credit_days`.
  - `MapTrace` records model, tokens, retrieved chunk IDs.
  - Usage row written.
- **Done when:** `pytest packages/mapper/` passes.

---

## Wave 4 — Vision + risk + summary + eval (M3)

### Task 10: Vision extract path
- **Agent:** `p1-extract` (vision concern)
- **Reads:** `person1_ai_rag/ARCHITECTURE.md` vision section, contracts
- **Writes:**
  - `packages/extract/vision_fallback.py`
  - `packages/extract/prompts/extract_vision.txt`
  - `packages/extract/tests/test_extract_vision.py`
  - `packages/extract/tests/fixtures/` (≥2 scan/photo fixtures: page images + expected JSON)
- **Tests:**
  - Only activates when `raw_markdown` is empty/missing.
  - Returns valid `CanonicalInvoice` on scan fixtures.
  - Usage row written.
- **Done when:** `pytest packages/extract/tests/test_extract_vision.py` passes.

### Task 11: Risk LLM worker (narrow marks)
- **Agent:** ad-hoc worker
- **Reads:** `person1_ai_rag/ARCHITECTURE.md` control section, `shared/00_SHARED_CONTRACTS.md` RiskScore
- **Writes:**
  - `packages/risk_llm/risk_llm_worker.py`
  - `packages/risk_llm/tests/test_risk_marks.py`
- **Tests:**
  - Outputs only LLM-specific marks (fraud hints, language anomalies).
  - Does NOT compute GSTIN checksum, tax math, duplicate key (Person 3's job).
  - Usage row written.
- **Done when:** `pytest packages/risk_llm/` passes.

### Task 12: AI approval paragraph
- **Agent:** ad-hoc worker
- **Reads:** `person1_ai_rag/ARCHITECTURE.md` summary section, contracts
- **Writes:**
  - `packages/summary/summary_writer.py`
  - `packages/summary/tests/test_summary.py`
- **Tests:**
  - Output is 4–6 lines.
  - Mentions vendor name, amount, GST treatment, risk band, mapping rationale.
  - Usage row written.
- **Done when:** `pytest packages/summary/` passes.

### Task 13: Eval harness + cost dashboard
- **Agent:** `p1-eval`
- **Reads:** All contracts, extract fixtures, mapper fixtures
- **Writes:**
  - `packages/eval/harness.py`
  - `packages/eval/metrics.py`
  - `packages/eval/dashboard.py`
  - `packages/eval/tests/test_harness.py`
- **Tests:**
  - Runs over 30–50 fixture bills.
  - Reports: field F1 (per-field precision/recall), ledger accuracy, tokens/bill, cost/bill, review-escape rate.
  - Dashboard prints summary table.
- **Done when:** `pytest packages/eval/` passes. Dashboard renders a readable summary.

---

## Wave 5 — Correction memory + tuning (M4)

### Task 14: Memory indexer (CorrectionEvent → memory chunk)
- **Agent:** ad-hoc worker (or `p1-retriever` extension)
- **Reads:** `shared/00_SHARED_CONTRACTS.md` CorrectionEvent, RAG indexer from Task 7
- **Writes:**
  - `packages/memory/memory_indexer.py`
  - `packages/memory/tests/test_memory_reuse.py`
- **Tests:**
  - CorrectionEvent "HSN 9983 → Professional fees 194J" for vendor X upserts a memory chunk.
  - Next map call for vendor X retrieves that chunk.
  - Map output reflects the correction (ledger = Professional fees, TDS = 194J).
- **Done when:** End-to-end test: correct → index → retrieve → map passes.

### Task 15: Posted-bill indexer
- **Agent:** ad-hoc worker
- **Reads:** RAG indexer from Task 7
- **Writes:**
  - Update `packages/rag/indexer.py` or `packages/memory/memory_indexer.py` to handle `posted_bill` chunk type.
  - `packages/rag/tests/test_posted_bill_index.py`
- **Tests:**
  - After post confirmation (`doc_id`, `erp_id`), a `posted_bill` chunk is indexed under the correct `client_id` namespace.
  - Retriever can find it for the same vendor's future bills.
- **Done when:** Test passes.

### Task 16: Prompt tuning pass
- **Agent:** ad-hoc worker
- **Reads:** Eval harness output from Task 13, correction memory from Task 14
- **Writes:**
  - Updated prompts in `packages/extract/prompts/` and `packages/mapper/prompts/`
  - Eval harness comparison report (before/after)
- **Tests:** Re-run eval harness. Metrics should improve or hold (no regression).
- **Done when:** Comparison report shows no field F1 or ledger accuracy regression.

---

## Dependency graph

```
Wave 1: [Task 1] → [Task 2]
         [Task 3] → [Task 4]

Wave 2: [Task 1,3,4] → [Task 5]
         [Task 1] → [Task 6]

Wave 3: [Task 1] → [Task 7] → [Task 8]
         [Task 1,5,8] → [Task 9]

Wave 4: [Task 5] → [Task 10]
         [Task 1] → [Task 11]
         [Task 1,9] → [Task 12]
         [Task 5,6,9] → [Task 13]

Wave 5: [Task 7,9] → [Task 14]
         [Task 7] → [Task 15]
         [Task 13,14] → [Task 16]
```

---

## Concurrency rules

- Max 3 Gemini Pro subagents at a time.
- Within a wave, independent tasks can run in parallel (e.g., Task 1 ∥ Task 3).
- Cross-wave tasks must wait for their dependencies.
- After each wave, orchestrator (Opus) reviews diffs against refusals before starting next wave.
