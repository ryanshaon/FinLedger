-- Person 3 records, persisted inside Person 2's RLS boundary.

create table canonical_invoices (
  document_id  uuid primary key references documents(id) on delete cascade,
  client_id    uuid not null references clients(id) on delete cascade,
  payload      jsonb not null check (jsonb_typeof(payload) = 'object'),
  llm_score    int not null default 0 check (llm_score between 0 and 100),
  llm_marks    jsonb not null default '[]' check (jsonb_typeof(llm_marks) = 'array'),
  maker_id     uuid not null references users(id),
  revision     int not null default 1 check (revision > 0),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create table risk_assessments (
  id           bigint generated always as identity primary key,
  client_id    uuid not null references clients(id) on delete cascade,
  document_id  uuid not null references documents(id) on delete cascade,
  score        int not null check (score between 0 and 100),
  band         text not null check (band in ('low','medium','high')),
  marks        jsonb not null check (jsonb_typeof(marks) = 'array'),
  created_at   timestamptz not null default now()
);
create index risk_latest on risk_assessments(client_id, document_id, created_at desc);

create table voucher_drafts (
  document_id  uuid primary key references documents(id) on delete cascade,
  client_id    uuid not null references clients(id) on delete cascade,
  payload      jsonb not null check (jsonb_typeof(payload) = 'object'),
  revision     int not null default 1 check (revision > 0),
  created_by   uuid not null references users(id),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create table review_tasks (
  id            bigint generated always as identity primary key,
  client_id     uuid not null references clients(id) on delete cascade,
  document_id   uuid not null references documents(id) on delete cascade,
  status        text not null default 'open' check (status in ('open','approved','rejected','resubmit','cancelled')),
  risk_band     text not null check (risk_band in ('low','medium','high')),
  reason        text not null,
  assigned_to   uuid references users(id),
  decided_by    uuid references users(id),
  decision_note text,
  created_at    timestamptz not null default now(),
  decided_at    timestamptz,
  unique (document_id, status) deferrable initially immediate
);
create index review_inbox on review_tasks(client_id, status, risk_band, created_at);

create table correction_events (
  id            bigint generated always as identity primary key,
  client_id     uuid not null references clients(id) on delete cascade,
  document_id   uuid not null references documents(id) on delete cascade,
  vendor_gstin  text,
  field         text not null,
  old_value     jsonb,
  new_value     jsonb,
  hsn           text,
  description   text,
  actor_id      uuid not null references users(id),
  created_at    timestamptz not null default now()
);
create index corrections_for_rag on correction_events(client_id, vendor_gstin, created_at);

create table approvals (
  id            bigint generated always as identity primary key,
  client_id     uuid not null references clients(id) on delete cascade,
  document_id   uuid not null references documents(id) on delete cascade,
  kind          text not null check (kind in ('human','policy_clear','payment_release')),
  actor_id      uuid references users(id),
  revision      int not null,
  created_at    timestamptz not null default now(),
  unique (document_id, kind, revision)
);

create table workflow_events (
  id            bigint generated always as identity primary key,
  client_id     uuid not null references clients(id) on delete cascade,
  document_id   uuid not null references documents(id) on delete cascade,
  from_status   text not null,
  to_status     text not null,
  action        text not null,
  actor_id      uuid references users(id),
  metadata      jsonb not null default '{}',
  created_at    timestamptz not null default now()
);
create index workflow_audit on workflow_events(client_id, document_id, created_at);

do $$
declare t text;
begin
  foreach t in array array['canonical_invoices','risk_assessments','voucher_drafts','review_tasks',
                           'correction_events','approvals','workflow_events'] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy tenant on %I using (client_id = app_client_id()) with check (client_id = app_client_id())', t);
  end loop;
end $$;

grant select, insert, update, delete on canonical_invoices, risk_assessments, voucher_drafts,
  review_tasks, correction_events, approvals, workflow_events to finledger_app;
grant usage on all sequences in schema public to finledger_app;

