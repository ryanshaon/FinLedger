# FinLedger Control & Review — Person 3

Deterministic AP controls, workflow ownership, separation of duties, exception handling, correction memory, and the reviewer desk. This package never extracts documents and never translates a draft into Tally XML.

## Architecture

```text
Person 1 CanonicalInvoice + LLM marks
                  │
                  ▼
       deterministic risk controls ──────┐
                  │                      │ field-level marks
                  ▼                      ▼
            low / medium / high ──► ReviewTask
                  │                      │
           policy-clear             human decision
                  └──────────┬───────────┘
                             ▼
                    approved VoucherDraft
                             │
                     idempotent post job
                             ▼
                         Person 4
```

The UI and API call `ControlService`; they never update `documents.status` or enqueue a post directly.

## What ships

- Additive risk scoring with field-level evidence. LLM risk can raise the final score but cannot lower deterministic controls.
- Client-scoped duplicate detection using vendor GSTIN + invoice number + financial year + amount.
- Indian AP checks: buyer GSTIN identity, intra/inter-state GST shape, standard tax rates, IRN soft warning, TDS flag, MSME 45-day clock, and 180-day ITC warning.
- Process checks: confidence, total reconciliation, unreadable pages, new vendor, bank change, sender mismatch, PO/GRN policy, amount spike, and future date.
- Explicit state transitions and append-only `workflow_events`.
- Review routing for Medium, High, maker-checker, and low bills above the auto-post cap.
- Three-column reviewer desk with queue views: New, Needs review, Approved, Posted, Exception.
- Edit → `CorrectionEvent`; draft revisions make stale approvals fail.
- Human/policy approval audit, no self-approval, and approver/payment-releaser separation.
- Person 4 failures move the document to `exception`; they do not create another post job.

## Database

Migration `person2_platform/.../004_control_review.sql` adds `canonical_invoices`, `risk_assessments`, `voucher_drafts`, `review_tasks`, `correction_events`, `approvals`, and `workflow_events`. Every table has `client_id`, PostgreSQL RLS, and the same fail-closed tenant context as Person 2.

## Run

```powershell
uv sync
$env:FINLEDGER_DATABASE_URL = "postgresql://fl_app:...@localhost/finledger"
$env:FINLEDGER_STORE = "local"
$env:FINLEDGER_STORE_ROOT = "../person2_platform/var/objects"
$env:FINLEDGER_SIGNING_SECRET = "same-secret-as-person-2"
$env:FINLEDGER_APP_BASE_URL = "http://localhost:8000"
uv run finledger-control --port 8770
```

Person 2 serves signed `/files/{token}` URLs. For S3, set `FINLEDGER_STORE=s3` and `FINLEDGER_S3_BUCKET`. Staff requests use Person 2 bearer tokens. In browser deployment, the reverse proxy/SSO layer must inject the bearer credential for page and form requests; tokens must never appear in URLs.

## Tests

```powershell
uv run pytest -p no:cacheprovider
```

The integration suite starts a temporary PostgreSQL cluster and exercises real RLS.

## Operational invariants

- A High or Medium bill has no post job until valid approval.
- `jobs(queue='post')` is the only handoff to Person 4.
- An approval applies to one draft revision; material edits require approval of the new revision.
- `CorrectionEvent` is append-only and client-scoped.
- ERP failure is a workflow exception. Person 4 owns adapter retry safety and idempotency.

