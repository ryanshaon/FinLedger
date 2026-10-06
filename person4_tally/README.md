# Person 4 — Tally Connector

> Status: planned; not runnable.

This folder currently contains the connector architecture only. The Windows
agent, pairing flow, Tally adapter, installer, posting implementation, attachment
handling, master synchronization, and automated tests have not been built.

The intended security boundary is a locally installed Windows service that
accepts authenticated, revision-bound posting jobs. Business idempotency is
`document_id`; each attempt is identified by `(document_id, revision)` and must
return its job ID, revision, idempotency key, ERP ID, and result to Person 3.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the planned interface and prerequisites.
