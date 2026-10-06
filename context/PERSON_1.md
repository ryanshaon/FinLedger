# Person 1 — live context

Seat: AI / RAG / tokens. Last updated: 2026-10-06

## Owns
Extract LLM, risk-LLM slice, client RAG, VoucherDraft, prompts, token budget, correction memory, MapTrace.

## Does not own
Queues, object store, state transitions, review chrome, Tally XML.

## Current status
- Source, tests, fixtures, prompts, evaluations, and plans are merged.
- Contracts, digital extraction, isolated in-memory RAG, mapping, narrow risk
  marks, summaries, usage logging, and token caps are implemented.
- Cross-seat contracts accept Person 3 corrections and nullable due dates.
- Provider mode is mock. Live-provider and persistent-RAG adapters remain future work.

## Models
- provider: `FINLEDGER_LLM_PROVIDER=mock`
- extract: `FINLEDGER_MODEL_EXTRACT=mock-extract-model`
- vision: `FINLEDGER_MODEL_EXTRACT_VISION=mock-extract-vision-model`
- risk: `FINLEDGER_MODEL_RISK=mock-risk-model`
- map: `FINLEDGER_MODEL_MAP=mock-map-model`
- summary: `FINLEDGER_MODEL_SUMMARY=mock-summary-model`
- embedding: `FINLEDGER_EMBEDDING_MODEL=mock-embedding-model`
- per-bill token cap: `FINLEDGER_PER_BILL_TOKEN_CAP=12000`
- kill switch: enabled; budget is reserved before generation

## Index layout
Namespace = `client_id`. Chunk types: `ledger | vendor | posted_bill | memory | policy | hard_rule`.

## Integration notes
- P2 paths travel in document/job payloads and object-store keys.
- P3 field-oriented correction payloads are accepted by Person 1.
- P4 master sync and posted-bill callbacks remain connector work.

## Do not
Global RAG. Raw pixels into mapper first. LLM XML. Auto-create vendor. Invent totals.
