# Person 1 — AI, RAG, and Token Controls

This seat owns invoice extraction, client-isolated retrieval, ERP-neutral voucher
mapping, narrow LLM risk marks, reviewer summaries, usage logging, and token caps.
It never posts to an ERP.

## Source layout

- `../packages/contracts`: Pydantic contracts, JSON Schemas, golden invoices.
- `../packages/extract`: digital extraction worker and prompt.
- `../packages/rag`: tenant-scoped indexer and retriever.
- `../packages/mapper`: invoice-to-`VoucherDraft` mapping.
- `../packages/safety`, `summary`, `usage`: risk marks, summaries, usage controls.

## Run locally

```powershell
$env:PYTHONPATH = "packages"
python -m pytest packages -q
python evals/run_evals.py
```

The development provider is `mock`; `.env` contains placeholders only. See
[ARCHITECTURE.md](ARCHITECTURE.md) and the
[implementation plan](../docs/plans/person1_implementation_plan.md).
