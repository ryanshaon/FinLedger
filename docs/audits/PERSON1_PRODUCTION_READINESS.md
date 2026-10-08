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

## Extract consumer: implementation plan (documentation only, 2026-10-09 overnight run)

**Status: plan only. No consumer code was written.** Tracing the current code found three blockers that need owner
decisions, so wiring a worker now would either guess policy or ship something the runtime cannot run.

### What exists today (traced)

| Step | Code | State |
|---|---|---|
| Producer | `person2_platform/.../worker.py::process_ingest` enqueues `extract` with `extract_payload()` (contract in `context/HANDOFFS.md`, 2026-09-28) inside `tenant()` | live, tested |
| Input loader | `worker.py::load_extract_input(conn, store, job)` resolves markdown + layout from the object store | exists, unused |
| Queue | `claim_job` / `finish_job` (migration 005): SKIP LOCKED lease (300 s), `max_attempts` 5, `permanent=True` → `dead`, `requeue_dead_job` only for `finledger_queue_ops`. Claim/finish are granted to `finledger_worker_extract` | live, tested |
| P1 extraction | `packages/extract/vision_fallback.py::ExtractionDispatcher.extract(client_id, document_id, markdown, page_image_paths)` → `CanonicalInvoice` | mock provider only |
| P3 scoring | `ControlService.score(client_id, document_id, invoice, actor_id)` requires `documents.status = 'extracted'` and writes `canonical_invoices.maker_id = actor_id` (`NOT NULL`, FK `users`) | live, tested with human actors |
| Status `received → extracted` | **nothing sets it** | gap |
| Runtime image | `Dockerfile` copies only `person2_platform` and `person3_control_ui`; `packages/` is not installed anywhere | gap |

### Blocking decisions (owner)

1. **System maker identity.** Automated extract/score needs an `actor_id`. Options: (a) one non-login
   "FinLedger automation" `users` row per firm (no `auth_subject`, no API token, never a firm admin), or (b) make
   `canonical_invoices.maker_id` nullable with `maker_kind = 'system'`. Either one changes maker-checker
   semantics: a human approver will always differ from the system maker, so SoD must be defined against the human
   who *edits* (correction events), not the automated maker. Recommendation: (a), plus a P3 rule that
   auto-post requires no human edits or a second human.
2. **Provider and PII data flow** (already a P0 above): region, retention, logging, and whether page images may
   leave FinLedger. Until approved, the consumer must refuse to start in staging/production. `ModelRouter`
   already fails closed on `mock` there.
3. **Statements and CSV rows.** `document_class = statement` is enqueued today. Decide: skip with
   `status = 'exception'` and reason `statement not extracted`, or extract. Recommendation: skip for v1.

### Packaging (pre-requisite, no policy needed)

Give Person 1 a `pyproject.toml` (package `packages/*` as `finledger_ai`, or install the existing top-level
packages) and add it as a path dependency of `person2_platform`. Run `uv lock` in both P2 and P3 and
`uv lock --check`. Add an image check: `python -c "import extract.vision_fallback"`.

### Consumer design (once 1–3 are answered)

Entry point: `finledger-platform extract-worker`, running as a login role that is a member of `finledger_app` and
`finledger_worker_extract` only (not ingest/score/post).

```
job = claim(conn, "extract")                       # outside tenant
with tenant(conn, job.client_id):                  # all reads/writes RLS-scoped
    doc = select status, document_class, content_hash from documents where id = job.document_id
    if doc is None:                 finish(permanent, "document row missing")
    if doc.status != 'received':    finish(ok)       # replay: already extracted/handled
    if document_class == statement: set status 'exception' (decision 3); finish(ok)
input = load_extract_input(conn, store, job)       # object keys must start with f"{client_id}/"; assert it
invoice = dispatcher.extract(client_id, document_id, input.raw_markdown, input.page_image_paths)
with tenant(conn, job.client_id):                  # one transaction
    update documents set status='extracted' where id=… and status='received'   # compare-and-set
    enqueue(conn, "score", client_id, document_id, {"canonical_invoice": invoice.model_dump(mode="json")})
finish(ok)
```

Then a `score` consumer calls `ControlService.score(..., actor_id=<system maker>)` and `submit_draft` after mapping.
Keeping extract and score as separate jobs gives separate retry budgets and keeps model calls out of the scoring
transaction. Alternative: the extract consumer calls `score` directly, which needs no new job payload and avoids
storing invoice fields in `jobs.payload`. Pick one with decision 2. Storing extracted fields in `jobs.payload`
duplicates PII that `canonical_invoices` stores anyway.

Error mapping:

| Failure | Outcome |
|---|---|
| Provider timeout / 5xx / rate limit | `finish(ok=False)` → retry with backoff, `dead` after 5 |
| Token cap (`TokenCapExceededException`) | permanent → `dead`, document `exception` "AI budget exceeded" |
| Invalid JSON / schema (`ValueError`, `ValidationError`) | retry once (models are stochastic), then permanent |
| Vision page cap exceeded | permanent; document `exception` |
| Object key outside `client_id/` | permanent; never read |
| Lost lease (`finish` returns `None`) | drop result; the next claimant redoes the work |

`last_error` must hold only exception class names and fixed messages, never invoice text. That is a PII rule
the current ingest worker does not follow (`f"{type(e).__name__}: {e}"`).

### Tests to write with the consumer (ephemeral Postgres, fake provider)

- Claim → extract → `extracted` + one `score` job, under the non-owner `fl_app` role. A second client's job never
  reads the first client's rows or object keys.
- A replayed job (status already `extracted`) is a no-op. Duplicate `score` enqueue is prevented by
  `(queue, document_id, idem_key)`.
- Transient failures retry, then `dead`. Permanent failures go `dead` immediately. `requeue_dead_job` is only
  callable by `finledger_queue_ops`.
- `last_error` never contains markdown or field values (assert with a canary string in the fixture).
- Mock provider refused when `FINLEDGER_ENV` is staging/production (startup test).
- Lease expiry mid-extract: the late `finish` returns `None` and does not overwrite the new claimant's work.

### Not claimed

There is no extract → score → map E2E, no live provider and no persistent RAG/usage state. Mock evals remain
contract checks only.
