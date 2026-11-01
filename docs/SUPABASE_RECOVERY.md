# FinLedger replacement database recovery

Owner-authorized destination: **StartUps / FinLedger**, project
`smnfnnwmasnsuuwrboge`, Mumbai (`ap-south-1`). The old project
`hxymklwqifwojziewtcv` is a historical source, not this restore's destination.

## Source and preflight

- Private source: `db_cluster-15-10-2026@16-09-17.backup.gz`, 74,244 bytes.
- SHA-256: `025d1668ae03c483ada22389da3c868185e95b66476a16089e5c8a72f1dc8d8d`.
- Readable PostgreSQL cluster dump with completion marker; 65 table definitions.
- All 23 application data tables, both private staff-session tables, `auth.users`
  and `storage.objects` contain zero rows. `public.schema_migrations` contains
  exactly migrations 001-011. The only configuration row to restore is the
  private `finledger-documents-dev` bucket (25 MiB; no MIME restriction).
- Destination preflight: PostgreSQL 17.11; no public/private application tables,
  no Auth users, no buckets and no storage objects.

The raw backup stays outside Git and is not executed as a cluster dump. It
contains Supabase-managed schemas and role definitions that must not overwrite
the replacement project's platform configuration or credentials. The application
schema is reconstructed from the exact committed migrations matching its tracker;
there are no application records or uploaded objects to import. Platform migration
histories are not customer data and are not copied between project versions.

## Execution order

All SQL below is committed under
`person2_platform/src/finledger_platform/migrations/` before execution. Files in
its `recovery/` subdirectory are operator-only; the app migrator reads only SQL
files immediately inside `migrations/`.

1. Confirm this exact destination remains empty. In one atomic hosted migration,
   concatenate `recovery/backup_20261015_bootstrap.sql`, the unmodified migrations
   001-011 in filename order, `recovery/backup_20261015_history.sql`, and
   `recovery/backup_20261015_bucket.sql`. Do not expose an intermediate schema
   before migration 006 removes Data API grants.
2. Verify restored baseline: exact table/column inventory against the backup,
   11 app migration records, tenant RLS, zero application rows, private bucket.
3. In a second atomic hosted migration, concatenate unmodified migrations
   012-015 and `recovery/current_015_history.sql`. These are forward upgrades for
   the current application, not contents of the October 15 backup.
4. Verify 15 app migration records, added tables/columns/functions, RLS and API
   grants; run rollback-only cross-tenant checks and security advisors.
5. Record actual results below and in root HANDOVER. Do not replay after success.

Supabase's own migration history records the two recovery batches. The app's
`public.schema_migrations` records the individual filenames, so app startup will
not attempt to repeat their DDL. `applied_at` records restoration, not the old
deployment timestamp. Original role passwords, JWT keys, API/S3 keys, Auth
dashboard settings and external integrations are not restored by this procedure.

## Verification and remaining setup

Execution is pending. This document is a plan until live results are recorded.
No replacement credentials or runtime `.env` changes have been made. Database
restoration alone does not connect the running app: restricted runtime logins,
new project keys/S3 credentials, Auth settings and live smoke tests still need
private configuration. Never substitute an owner or service-role connection for
the required RLS-enforced runtime role.

Reference: [Supabase backup and restore](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore).
