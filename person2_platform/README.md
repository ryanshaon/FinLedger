# finledger_platform — Person 2 (platform / intake / data)

```
link | email | CSV  →  auth + rate-limit + split + hash + store  →  documents row + ingest job
ingest_worker       →  virus scan → MarkItDown / layout / page images  →  extract job (Person 1)
```

Spec: `ARCHITECTURE.md` in this folder. Live context for the team: `../context/PERSON_2.md`.

## Layout

| File | What |
|---|---|
| `migrations/001_tenancy.sql` | firms, users, clients (+ policy columns), client_members, vendors |
| `migrations/002_documents_queue.sql` | documents, document_assets, jobs (+ `ingest_jobs` view), erp_links, mapping_table, masters_snapshots, open_bills, rag_chunks, outbox, rate_limits |
| `migrations/003_rls_functions.sql` | RLS on every tenant table, pre-tenant lookups, `claim_job` / `finish_job` / `requeue_dead_job`, grants |
| `db.py` | pool, `tenant()` / `firm()` scopes, RLS-bypass guard, migrator |
| `intake.py` | the single intake path every door uses |
| `email_in.py` | inbound email parsing, Authentication-Results, phish flag |
| `files.py` | magic-byte sniffing, ZIP split with bomb limits, filename hygiene |
| `virus.py` | clamd INSTREAM client (fail-closed) |
| `prep.py` | MarkItDown + pdfplumber layout + pypdfium2 page renders |
| `worker.py` | `ingest_worker` + the P2 → P1 payload |
| `queue.py` | enqueue / claim / finish for all five queues |
| `tenancy.py` | clients, tokens, GSTIN check, ERP link + encrypted creds, masters, open bills, mapping, CSV rows |
| `store.py` | local + S3 object store, strict `client_id/yyyy/mm/doc_id/` keys, signed URLs |
| `api.py` / `web.py` | HTTP surface + vendor upload / receipt pages |

## Run it

```bash
uv sync
```

Environment (see `.env.example`), then:

```bash
finledger-platform migrate
```

Create the login role the app uses. It must be a member of `finledger_app` and must not own the tables:

```bash
psql -c "create role fl_app login password '…' in role finledger_app"
```

```bash
finledger-platform create-firm --name "Sharma & Co" --admin-email admin@example.in
```

```bash
finledger-platform serve --host 0.0.0.0 --port 8000
```

```bash
finledger-platform worker
```

Run as many workers as you like; claims use `SKIP LOCKED`. In production, set `FINLEDGER_VIRUS_SCANNER=clamd` (the default) and run `clamd`, and set `FINLEDGER_STORE=s3` with the `s3` extra installed.

## Test

```bash
uv run pytest
```

Boots a throwaway Postgres with `initdb`, or uses `FINLEDGER_TEST_OWNER_DSN`. The app side always connects as a non-owner role, so the tenancy tests run against real RLS.

## Acceptance tests → where they live

| Spec | Test |
|---|---|
| Two clients cannot read each other's files, rows, or embeddings | `test_tenancy.py` (rows, writes, rag_chunks, API 404s, signed URLs) |
| Upload via link creates a documents row and an extract job | `test_link.py::test_link_upload_creates_document_and_extract_job` |
| Email with one PDF creates the same shape as the link | `test_email.py::test_email_same_shape_as_link` |
| ZIP of two PDFs becomes two documents | `test_link.py::test_zip_of_two_pdfs_is_two_documents` |
| Virus-positive file never reaches extract | `test_link.py::test_virus_never_reaches_extract`, `test_queue.py::test_scanner_outage_retries_then_poisons` |
| Object path contains client_id | `test_tenancy.py::test_object_path_contains_client_id`, `…rejects_path_without_client_prefix` |
| Same bytes → same hash | `test_link.py::test_same_bytes_same_hash` |

## Not built (on purpose)

- Outbox mailer: replies are queued in `outbox`, and nothing sends them yet.
- Vendor portal and WhatsApp doors: they are phase 9, and intake is channel-agnostic (`intake.accept`).
- OCR: Person 1's vision path. P2 hands over page images for pages that have no text layer.
