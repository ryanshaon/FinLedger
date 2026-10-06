-- Documents, assets, the shared job queue, ERP/master storage, RAG tenancy, outbox.

create table documents (
  id                 uuid primary key default gen_random_uuid(),
  client_id          uuid not null references clients(id),
  channel            text not null check (channel in ('link', 'email', 'csv', 'portal', 'whatsapp')),
  source_meta        jsonb not null default '{}',   -- from, subject, ip, sender_domain, auth, archive, ...
  intake_key         text,                          -- dedupes provider webhook retries (email Message-ID + part)
  object_path        text not null,                 -- raw bytes
  original_filename  text not null default '',
  content_hash       text not null check (content_hash ~ '^[0-9a-f]{64}$'),  -- sha256 of raw bytes
  mime               text not null,
  size_bytes         bigint not null check (size_bytes > 0),
  page_count         int,
  document_class     text not null default 'invoice' check (document_class in ('invoice', 'statement', 'csv_row')),
  -- Person 3 owns every transition after 'received'. Person 2 only writes 'received', 'quarantined' (virus)
  -- and 'exception' (unreadable file, status_reason says why).
  status             text not null default 'received' check (status in (
                        'received', 'extracted', 'scored', 'vendor_resolved', 'matched', 'coded', 'tax_checked',
                        'in_review', 'approved', 'scheduled', 'paid', 'posted', 'reconciled', 'closed',
                        'exception', 'rejected', 'resubmit', 'quarantined')),
  status_reason      text,
  virus_ok           boolean,                       -- null = not scanned yet
  phish_flag         boolean not null default false,
  po_number          text,
  vendor_note        text,
  received_at        timestamptz not null default now(),
  prepared_at        timestamptz,                   -- markdown/images written, extract enqueued
  updated_at         timestamptz not null default now(),
  unique (client_id, intake_key),
  -- The refusal "global bucket without client_id prefix", enforced by the database.
  check (object_path like client_id::text || '/%')
);
create index documents_queue on documents (client_id, status, received_at desc);
create index documents_hash on documents (client_id, content_hash);

create table document_assets (
  id           bigint generated always as identity primary key,
  client_id    uuid not null references clients(id),
  document_id  uuid not null references documents(id) on delete cascade,
  kind         text not null check (kind in ('raw', 'markdown', 'page_image', 'layout_json')),
  path         text not null,
  page_no      int,
  created_at   timestamptz not null default now(),
  unique nulls not distinct (document_id, kind, page_no),
  check (path like client_id::text || '/%')
);

-- One queue table, five queue names. Person 2 owns names, leases, retries and the poison state.
-- Every seat claims with claim_job(queue) and settles with finish_job(...).
create table jobs (
  id            bigint generated always as identity primary key,
  client_id     uuid not null references clients(id),
  document_id   uuid not null references documents(id) on delete cascade,
  queue         text not null check (queue in ('ingest', 'extract', 'score', 'map', 'post')),
  idem_key      text not null default '',          -- same (queue, document, idem_key) is enqueued once
  payload       jsonb not null default '{}',
  state         text not null default 'queued' check (state in ('queued', 'running', 'done', 'dead')),
  attempts      int not null default 0,
  max_attempts  int not null default 5 check (max_attempts > 0),
  run_after     timestamptz not null default now(),
  locked_until  timestamptz,
  last_error    text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (queue, document_id, idem_key)
);
create index jobs_ready on jobs (queue, run_after) where state in ('queued', 'running');
create index jobs_dead on jobs (queue, updated_at) where state = 'dead';

-- The spec's ingest_jobs table, as a tenant-safe view over the shared queue.
create view ingest_jobs with (security_invoker = true) as
  select id, client_id, document_id, state, attempts, last_error, created_at, updated_at
  from jobs where queue = 'ingest';

-- Person 4: agent pairing + credentials. Pairing token is hashed (for lookup), ERP secrets are encrypted.
create table erp_links (
  client_id         uuid primary key references clients(id) on delete cascade,
  erp_type          text not null,
  company_name      text,
  agent_token_hash  bytea unique,
  credentials_enc   bytea,                         -- Fernet ciphertext, key outside the database
  paired_at         timestamptz,
  last_seen_at      timestamptz
);

-- Person 4: our key -> ERP name (GSTIN -> party ledger, ledger -> Tally ledger, tax -> input ledgers).
create table mapping_table (
  id          bigint generated always as identity primary key,
  client_id   uuid not null references clients(id) on delete cascade,
  kind        text not null check (kind in ('party', 'ledger', 'tax', 'tds', 'cost_centre')),
  our_key     text not null,
  erp_value   text not null,
  updated_at  timestamptz not null default now(),
  unique (client_id, kind, our_key)
);

-- Person 4 writes, Person 1 embeds. Append-only; latest row per (client, kind) is current.
create table masters_snapshots (
  id          bigint generated always as identity primary key,
  client_id   uuid not null references clients(id) on delete cascade,
  kind        text not null check (kind in ('ledger', 'group', 'vendor', 'cost_centre', 'company')),
  items       jsonb not null check (jsonb_typeof(items) = 'array'),
  item_count  int generated always as (jsonb_array_length(items)) stored,
  source      text not null default 'tally_agent',
  fetched_at  timestamptz not null default now()
);
create index masters_latest on masters_snapshots (client_id, kind, fetched_at desc);

-- Person 4 outstanding pull -> ageing views (Person 3). Replaced wholesale per pull.
create table open_bills (
  id              bigint generated always as identity primary key,
  client_id       uuid not null references clients(id) on delete cascade,
  party_ledger    text not null,
  vendor_gstin    text,
  bill_ref        text not null,
  bill_date       date,
  due_date        date,
  amount          numeric(14,2) not null,
  pending_amount  numeric(14,2) not null,
  as_of           timestamptz not null
);
create index open_bills_client on open_bills (client_id, due_date);

-- Person 1 writes RAG chunks; Person 2 owns the tenancy. Namespace = client_id, enforced by RLS.
create table rag_chunks (
  id          bigint generated always as identity primary key,
  client_id   uuid not null references clients(id) on delete cascade,
  chunk_type  text not null check (chunk_type in ('ledger', 'vendor', 'posted_bill', 'memory', 'policy', 'hard_rule')),
  ref         text not null default '',
  content     text not null,
  metadata    jsonb not null default '{}',
  created_at  timestamptz not null default now()
);
create index rag_chunks_client on rag_chunks (client_id, chunk_type);
-- Embedding column: pgvector when installed (production), real[] otherwise so dev/test still run.
-- Person 1 sets the dimension and ANN index once the embedding model is chosen.
do $$ begin
  begin
    create extension if not exists vector;
  exception when others then
    raise notice 'pgvector not available; rag_chunks.embedding falls back to real[]';
  end;
  if exists (select from pg_extension where extname = 'vector') then
    execute 'alter table rag_chunks add column embedding vector';
  else
    execute 'alter table rag_chunks add column embedding real[]';
  end if;
end $$;

-- Replies to vendors ("received" / "resubmit"). A mailer drains this; Person 2 only writes it.
create table outbox (
  id           bigint generated always as identity primary key,
  client_id    uuid not null references clients(id) on delete cascade,
  document_id  uuid references documents(id) on delete set null,
  to_addr      text not null,
  template     text not null check (template in ('received', 'resubmit')),
  payload      jsonb not null default '{}',
  state        text not null default 'queued' check (state in ('queued', 'sent', 'failed')),
  created_at   timestamptz not null default now()
);

-- Fixed-window counters for the public doors (per token, per IP). Not tenant data.
create table rate_limits (
  key           text not null,
  window_start  timestamptz not null,
  hits          int not null default 0,
  primary key (key, window_start)
);
