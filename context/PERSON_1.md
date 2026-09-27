# Person 1 — live context

Seat: AI / RAG / tokens. Last updated: 2026-09-28

## Owns
Extract LLM, risk-LLM slice, client RAG, VoucherDraft, prompts, token budget, correction memory, MapTrace.

## Does not own
Queues, object store, state transitions, review chrome, Tally XML.

## Current status
- Architecture frozen. Code not started in this pack.
- Contracts live in `shared/00_SHARED_CONTRACTS.md`.
- Product story in `docs/PRODUCT.md` and `docs/HOW_IT_WORKS.md`.

## Models (fill when chosen)
- extract:
- extract_vision:
- risk_llm:
- map:
- summary:
- per-bill token cap:
- kill switch:

## Index layout
Namespace = `client_id`. Chunk types: `ledger | vendor | posted_bill | memory | policy | hard_rule`.

## Open questions for others
- P2: exact paths for `raw_markdown` and page images?
- P3: CorrectionEvent field list signed off?
- P4: when do masters land so we can embed?

## Do not
Global RAG. Raw pixels into mapper first. LLM XML. Auto-create vendor. Invent totals.
