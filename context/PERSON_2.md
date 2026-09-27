# Person 2 — live context

Seat: platform / intake / data. Last updated: 2026-09-28

## Owns
Tenancy, RLS, unique link, inbound email, ingest, virus/hash, documents table, MarkItDown file prep.

## Current status
Architecture frozen. Code not started.

## Facts other people need
- Object path: `client_id/yyyy/mm/doc_id/`
- Queues: `ingest | extract | score | map | post`
- Doors week 0: unique link + inbound email only
- Personal Gmail / personal WhatsApp is not a door

## Open questions
- Inbound domain and slug pattern?
- Virus scanner choice?
- Postgres vs schema-per-firm for CA multi-tenant?
