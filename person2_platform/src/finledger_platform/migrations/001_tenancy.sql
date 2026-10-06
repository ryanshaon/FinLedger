-- FinLedger Person 2 — tenancy core.
-- One Postgres schema, every tenant row carries client_id, Row Level Security does the isolation.
-- The app connects as a member of role finledger_app, which is NOT the table owner and has no BYPASSRLS,
-- so every policy below applies to it. The tenant is set per transaction:
--     select set_config('app.client_id', '<uuid>', true)
-- No tenant set -> app_client_id() is NULL -> zero rows. Forgetting to set it fails closed.

do $$ begin
  if not exists (select from pg_roles where rolname = 'finledger_app') then
    create role finledger_app nologin;
  end if;
end $$;

create or replace function app_client_id() returns uuid
language sql stable as $$ select nullif(current_setting('app.client_id', true), '')::uuid $$;

create or replace function app_firm_id() returns uuid
language sql stable as $$ select nullif(current_setting('app.firm_id', true), '')::uuid $$;

-- A CA firm (or an SMB running itself) owns many clients.
create table firms (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  created_at  timestamptz not null default now()
);

create table users (
  id              uuid primary key default gen_random_uuid(),
  firm_id         uuid not null references firms(id),
  email           text not null unique,
  name            text not null default '',
  firm_admin      boolean not null default false,
  api_token_hash  bytea unique,              -- sha256 of the bearer token; plaintext never stored
  created_at      timestamptz not null default now()
);

create table clients (
  id                uuid primary key default gen_random_uuid(),
  firm_id           uuid not null references firms(id),
  name              text not null,
  erp_type          text not null default 'tally' check (erp_type in ('tally', 'zoho', 'qbo', 'sap', 'oracle')),
  gstins            text[] not null default '{}',
  public_token      text not null unique,     -- the /i/{token} upload link; rotatable
  inbound_slug      text not null unique check (inbound_slug ~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$'),
  -- Policy flags: Person 3 defines and enforces them, Person 2 stores them, Person 1 reads them.
  auto_post_cap     numeric(14,2) not null default 0 check (auto_post_cap >= 0),  -- 0 = auto-post off
  po_required       boolean not null default false,
  po_required_above numeric(14,2) check (po_required_above >= 0),               -- null = every bill when po_required
  maker_checker     boolean not null default true,
  tds_sections      text[] not null default '{}',
  msme_clock        boolean not null default true,
  itc_180_warning   boolean not null default true,
  match_mode        text not null default 'invoice_only' check (match_mode in ('invoice_only', 'two_way', 'three_way')),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

-- Roles on a client (Person 3 enforces SoD on these).
create table client_members (
  client_id  uuid not null references clients(id) on delete cascade,
  user_id    uuid not null references users(id) on delete cascade,
  role       text not null check (role in ('ap_clerk', 'approver', 'payer')),
  primary key (client_id, user_id, role)
);

-- Vendor master. GSTIN is the real key. Person 2 persists, Person 1 resolves.
create table vendors (
  id             uuid primary key default gen_random_uuid(),
  client_id      uuid not null references clients(id) on delete cascade,
  gstin          text check (gstin ~ '^[0-9]{2}[A-Z0-9]{10}[0-9A-Z]Z[0-9A-Z]$'),
  name           text not null,
  pan            text,
  state_code     text,
  email_domains  text[] not null default '{}',   -- Person 3 compares documents.source_meta.sender_domain to this
  msme           boolean,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique nulls distinct (client_id, gstin)
);
create index vendors_client_name on vendors (client_id, lower(name));
