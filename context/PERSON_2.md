# Person 2 — live context

Seat: platform / intake / data. Last updated: 2026-09-28

## Owns
Tenancy, RLS, unique link, inbound email, ingest, virus/hash, documents table, MarkItDown file prep.

## Current status
Weeks 0–4 shipped in `person2_platform/` (Python package `finledger_platform`). 58 tests green against real Postgres RLS.
- W0 client workspace, RLS, object store, unique link, inbound email, documents table
- W1 MarkItDown + layout blocks + page images + extract job publish
- W2 masters write path (`/agent/masters`), CSV stub (`/clients/{id}/csv`)
- W3 queue leases, retries with backoff, poison (`dead`) state, clamd fail-closed, requeue endpoint
- W4 ageing storage (`/agent/open-bills` → `open_bills`)

## Facts other people need
- Object path: `client_id/yyyy/mm/doc_id/` — e.g. `…/2026/09/<doc>/raw.pdf`, `markdown.md`, `layout.json`, `pages/p001.png`. DB CHECK rejects any path without the `client_id/` prefix.
- Queues: `ingest | extract | score | map | post` — one `jobs` table. Claim with `claim_job(queue)` (works without a tenant), do the work inside `tenant(conn, job.client_id)`, settle with `finish_job(id, ok, error, permanent)`. Python: `finledger_platform.queue.claim / enqueue / finish`. `enqueue` is idempotent on `(queue, document_id, idem_key)`.
- Tenant context: `select set_config('app.client_id', '<uuid>', true)` inside a transaction (`db.tenant()`). No tenant → zero rows. The app role must be a member of `finledger_app`; the app refuses to start as superuser / owner / BYPASSRLS.
- Doors week 0: unique link `https://app…/i/{public_token}` + inbound email `invoices.{slug}@{FINLEDGER_INBOUND_DOMAIN}`.
- Personal Gmail / personal WhatsApp is not a door. Unknown recipient → dropped, never a fallback inbox.
- Document statuses P2 writes: `received` (all intake), `quarantined` (virus, never reaches extract, download refused), `exception` + `status_reason` (unreadable file). Every later transition is Person 3's.
- `documents.content_hash` = sha256 of raw bytes (P3 duplicate check). `documents.phish_flag` = SPF/DKIM/DMARC not passing, DKIM domain not aligned, or Reply-To domain ≠ From domain. `source_meta.sender_domain` is stored for P3's sender-vs-vendor-domain mark; `vendors.email_domains` holds known vendor domains.
- `documents.document_class`: `invoice | statement` (XLSX) `| csv_row`.
- Tables: `firms, users, clients (policy columns), client_members (ap_clerk|approver|payer), vendors, documents, document_assets, jobs (+ view ingest_jobs), erp_links, mapping_table, masters_snapshots, open_bills, rag_chunks, outbox, rate_limits`.
- For P1: `rag_chunks(client_id, chunk_type, ref, content, metadata, embedding)` is RLS-scoped; `embedding` is `vector` when pgvector is installed. Set the dimension + ANN index once the model is picked. Helper `worker.load_extract_input(conn, store, job)` returns the payload plus markdown text and layout dict.
- For P3: `GET /clients/{id}/documents?status=a,b&channel=&before=&limit=`, `GET …/documents/{doc}` (assets + jobs), `GET …/documents/{doc}/raw-url?kind=raw|page_image&page=` → 5-minute signed URL. Policy columns on `clients`: `auto_post_cap, po_required, po_required_above, maker_checker, tds_sections, msme_clock, itc_180_warning, match_mode`. `tenancy.gstin_valid()` is importable.
- For P4: pair an agent with `POST /clients/{id}/erp/pair` (firm admin, token shown once, stored hashed). Agent calls `/agent/masters`, `/agent/open-bills`, `PUT /agent/mapping` with `Authorization: Bearer fla_…`. Other ERP secrets: `tenancy.put_erp_credentials` (Fernet, key in `FINLEDGER_ENC_KEY`, never in the DB).
- Vendor replies (`received` / `resubmit`) are written to `outbox`, only for authenticated senders. A mailer to drain it is not built yet.

## Open questions
- Who runs the mailer that drains `outbox` (P2 infra or P3 notifications)?
- Production clamd host and inbound MX provider (SES / Mailgun / Postmark) — the webhook contract is raw MIME + `X-FinLedger-Signature: sha256=<hmac>`.
- P1: embedding dimension so `rag_chunks.embedding` can get a fixed type + HNSW index.
