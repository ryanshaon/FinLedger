# Decisions

Append-only. Newest at the bottom.

- 2026-09-23 — Architecture baseline is the source of truth. Extraction never posts.
- 2026-09-24 — Four seats: P1 AI/RAG, P2 platform, P3 control/UI, P4 Tally.
- 2026-09-24 — Tally loop before any second ERP, WhatsApp, or payment run.
- 2026-09-28 — Antigravity: Opus 4.6 plans only. Gemini Pro subagents implement. Shared context is markdown in `docs/` + `context/`.
- 2026-09-28 — Person 1 first Antigravity prompt is RAG + extract + map only. No UI, no Tally XML.
- 2026-09-28 — P2: Multi-tenancy is one Postgres schema + Row Level Security on `client_id`, not schema-per-firm. Why: one migration path, cross-client ops queries stay possible for the owner role, and RLS isolation is enforced by the database and covered by tests. App role is never owner/superuser/BYPASSRLS (checked at startup).
- 2026-09-28 — P2: Virus scanner is ClamAV (`clamd` INSTREAM over TCP). Fails closed: scanner down = job retry, then poison queue; a file is never marked clean without a scan.
- 2026-09-28 — P2: Inbound address is `invoices.{slug}@{FINLEDGER_INBOUND_DOMAIN}`; slug `^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$`, unique across all clients, default = slugified client name. MX provider posts raw MIME to `/inbound/email` with an HMAC signature; SPF/DKIM/DMARC read only from our MTA's Authentication-Results header.
- 2026-09-28 — P2: One `jobs` table serves all five queues (Postgres SKIP LOCKED leases). No Redis/SQS until volume proves the need.
- 2026-09-28 — P2: Virus scan + MarkItDown run in `ingest_worker`, not in the upload request, so vendors get an instant receipt and scanner outages never lose a bill.
- 2026-10-05 — P3: Deterministic risk is additive and capped at 100. Person 1's LLM risk may raise the merged score but never lower code controls. Marks are stored individually against fields.
- 2026-10-05 — P3: Human approval is revision-bound. A changed voucher draft invalidates the prior decision; stale reviewers get a conflict.
- 2026-10-05 — P3: Post handoff is one idempotent `post` job per document revision. The review UI never calls a Tally adapter directly.
- 2026-10-05 — P3: Review UI is server-rendered FastAPI HTML/CSS. Person 2 remains the source of signed document URLs.
