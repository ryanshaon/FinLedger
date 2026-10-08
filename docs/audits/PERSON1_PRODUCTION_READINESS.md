# Person 1 production-readiness audit

Status: **not ready for customer invoices**. Scope: read-only review of Person 1 packages and their Person 2 queue boundary, plus one fail-closed mock-provider guard. This is not an assessment of Person 4.

| Priority | Finding | Evidence | Required acceptance check |
|---|---|---|---|
| P0 | No live model provider exists; the router defaults to `mock` and rejects every other provider. | `packages/usage/model_router.py`, `packages/usage/provider.py` | Implement and test an approved provider adapter, with explicit model IDs, timeouts, retries, bounded payloads and cost accounting. The new guard must continue to reject mock in staging/production. |
| P0 | The queued extract job is not consumed by a Person 1 production worker. Person 2's worker enqueues `extract`, while its CLI starts only ingest/outbox workers. | `person2_platform/src/finledger_platform/worker.py`, `cli.py` | Run a queued document end-to-end through extract, score, map, review, and failure/retry/dead-letter paths under the non-owner app role. |
| P0 | Prompt/input and output PII controls are not evidenced. The vision path passes image *paths* to the provider contract, not bounded image bytes, and may not work with a real remote model. | `packages/extract/vision_fallback.py`, `extract_worker.py`, `usage/provider.py` | Decide permitted data flows/retention with the owner; enforce tenant-safe retrieval and provider data-handling policy; test redaction/minimization and real PDF/image handling without logging invoice content. |
| P1 | RAG index and retrieval are process-local, lexical simulations. Data disappears on restart and is not shared across workers. | `packages/rag/indexer.py`, `retriever.py` | Persist chunks/embeddings under tenant-scoped Postgres/RLS (or an approved equivalent), then test retrieval after restart and cross-tenant denial. |
| P1 | Usage records and token-cap reservations are process-local. Multi-worker or restart behavior can lose both cost audit and enforcement. | `packages/usage/usage_writer.py`, `kill_switch.py` | Persist usage and atomic reservations per tenant/document; test concurrency across separate processes and recovery after restart. |

## Safe implementation order

1. Keep staging/production fail-closed until a real provider and a queue consumer are wired. Never silently fall back to mock for customer documents.
2. Define the provider and data-handling policy (region, retention, logging, PII) before sending invoices outside FinLedger.
3. Build the provider adapter and persistent accounting; prove timeout, retry, cap and kill-switch behavior under concurrent workers.
4. Build the extract queue consumer and durable tenant-scoped retrieval, then run a real-document staging E2E path with no customer data.

Mock evaluation results remain useful contract checks but do not validate provider, persistence, or operational safety.
