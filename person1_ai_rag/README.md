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
uv sync --project person2_platform --python 3.12 --locked --all-extras --dev
$env:PYTHONPATH = "evals"
person2_platform/.venv/Scripts/python.exe -m pytest packages evals -q
person2_platform/.venv/Scripts/python.exe evals/run_evals.py --provider-mode mock
```

`packages/pyproject.toml` installs the existing top-level modules as the `finledger-ai`
distribution, a local dependency of Person 2 and a transitive dependency of Person 3.
The runtime image installs its wheel, including worker prompts and contract JSON Schemas;
tests and golden fixtures remain in the source checkout. No `PYTHONPATH=packages` is required.
Packaging does not add a live provider or an extract queue consumer.

The development provider is `mock`; staging and production reject it. See
[ARCHITECTURE.md](ARCHITECTURE.md) and the
[implementation plan](../docs/plans/person1_implementation_plan.md).
