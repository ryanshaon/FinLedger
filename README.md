# FinLedger AP — 4 person packs

Architecture baseline: 23 Sep 2026. Hard rule: extraction never posts.

| Pack | Seat | Markdown | PDF |
|---|---|---|---|
| Person 1 | AI / RAG / tokens | `person1_ai_rag/ARCHITECTURE.md` | `person1_ai_rag/Person1_AI_RAG.pdf` |
| Person 2 | Platform / intake / data | `person2_platform/ARCHITECTURE.md` | `person2_platform/Person2_Platform.pdf` |
| Person 3 | Control + review UI | `person3_control_ui/ARCHITECTURE.md` | `person3_control_ui/Person3_Control_UI.pdf` |
| Person 4 | Tally connector | `person4_tally/ARCHITECTURE.md` | `person4_tally/Person4_Tally_Connector.pdf` |

Shared objects: `shared/00_SHARED_CONTRACTS.md` and `shared/Shared_Contracts.pdf`.

The workspace contains the work for Persons 1–4. Harshith owns the Person 4 Tally connector;
its readiness is documented separately from the Persons 1–3 baseline below.

## Helping finish FinLedger

Start with [the three-person work split](docs/TEAM_WORK_SPLIT.md): AI, platform/deployment,
and controls/QA. Read [HANDOVER.md](HANDOVER.md) before changes. Do not rebuild completed work,
or share `.env`. Coordinate changes to `person4_tally/` with Harshith.

## Capability status

| Seat | Status | Source | Local validation |
|---|---|---|---|
| Person 1 | Implemented locally; live activation gated | `packages/`, `evals/` | 257 tests (`packages` + `evals`); mock evaluation 3/3 structural, 6/6 exact |
| Person 2 | Implemented | `person2_platform/` | 378 tests |
| Person 3 | Implemented | `person3_control_ui/` | 113 tests |
| Desktop | Implemented locally, not deployed | `desk/` | 8 Node helper tests, 14 Chromium checks (synthetic API), production build |
| Person 4 | Separately maintained; not assessed here | `person4_tally/` | See that pack's own evidence |

AI/platform baseline counts are from local Windows runs on 2 and 3 November 2026 (Python 3.12,
PostgreSQL 18). The complete control suite (113), Node helpers (8), Chromium checks (14), and production
build were verified on 4 November (Node 24.15.0). GitHub CI `test`, `image` and `desk` passed on initial
implementation `3acce09`; the final updated head must pass again before merging
[PR #54](https://github.com/ryanshaon/FinLedger/pull/54). Details:
[docs/RELEASE_EVIDENCE.md](docs/RELEASE_EVIDENCE.md).

`Implemented` means runnable and integration-tested against a throwaway local PostgreSQL. `Prototype` means
runnable with deterministic or mock development adapters. `Planned` means no runtime implementation exists.
Nothing is deployed and nothing is production ready: no live Auth, S3, scanner, AI provider, email, hosting
or Tally connection has been proven, and the restored hosted database has no runtime login. The local journey
tests use a mock provider and an EICAR scanner stub, not real OCR or ClamAV. Person 4 readiness is outside this assessment.

## Desktop MVP scope

The desktop in `desk/`, served by the Person 3 control server, supports:

- Staff sign in once and switch between the companies assigned to them.
- Upload of one PDF, JPG or PNG (up to 25 MiB) per request. Tenant, role (`ap_clerk` or firm admin), two-step
  verification and CSRF are checked before the body is read; membership and role are checked again before it is saved. The original is kept unchanged
  and can be opened once the virus scan has passed.
- Extraction view of vendor, GSTIN, invoice number, date, tax breakup and total, with every GST, duplicate and MSME
  check shown as passed, needs attention or not checked.
- Retrieval limited to the selected company's ledgers, vendors, earlier bills and corrections, plus copied shared
  GST/HSN hints. A saved correction wins and sets the party ledger; a memory whose known vendor GSTIN conflicts is excluded
  before ranking, and equally scored corrections rank newest first. The model is not retrained.
- Maker-checker: a person who edited a bill cannot approve it. Desk approval never queues a posting job, and the
  approval record is kept.

Not part of this MVP, even where code exists elsewhere in the repository: email intake, finished every-bill TDS,
purchase-order / goods-receipt / three-way matching on the main screen, outstanding bills, Tally posting or the post
blocker, bank matching, payments and GST filing. The full product list is a future target, not today's scope.

The dashboard shows review, processing, approval and exception counts with company queue shortcuts.
Search assigned companies by name or GSTIN in the header or Clients page; filter bills within a queue by
vendor, invoice number or filename. Refresh/retry controls and partial-load notices distinguish missing
counts from zero. Light and dark themes and keyboard skip navigation are supported.

## Prerequisites and verification

- Python 3.12 (the locked native dependencies do not support Python 3.14).
- `uv` for the Person 1–3 dependency environments.
- PostgreSQL tools (`initdb`, `pg_ctl`, `psql`) on `PATH` for integration tests.
- EICAR scanner mode for local tests; `clamd` is required in production.
- Node.js 24 and npm for the desktop in `desk/` (not covered by `scripts/test.ps1`).

Staging/production reject EICAR-only scanner configuration at settings construction.
Unknown scanner names are rejected in all environments rather than silently selecting a backend.

From this repository root, run the Python verification suites with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test.ps1
```

`scripts/test.ps1` does not run the desktop. From `desk/` (Node 24):

```powershell
npm ci
npm test
npm run build
npx --no-install playwright-core install chromium
npm run test:browser
```

`npm test` runs the Node helper tests; `npm run build` type-checks and writes `desk/dist`; `npm run test:browser`
drives that build in Chromium against a synthetic, intercepted API and needs the build first. The control server serves
`desk/dist` (override with `FINLEDGER_DESK_DIST`); the runtime image builds it in a Node 24 stage.

## Local mock environment

The repository root contains `.env.example` with placeholders for PostgreSQL,
Supabase, storage, email, Person 1 AI providers, and the Person 4 Tally agent.
Create a local copy only if `.env` does not already exist. Never overwrite existing credentials or distribute `.env`.

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
```

PowerShell does not automatically load `.env`. From this directory, load it into the current terminal before running either service:

```powershell
Get-Content .env | Where-Object { $_ -and -not $_.StartsWith('#') } | ForEach-Object {
  $name, $value = $_ -split '=', 2
  [Environment]::SetEnvironmentVariable($name, $value, 'Process')
}
```

Mock keys are suitable only for wiring and tests. Replace every `mock_`, `sk-mock`, `sk-ant-mock`, and local database password before deployment.

Release gates, staging proofs, scanner setup and safe rollback are documented in
[the deployment runbook](docs/DEPLOYMENT_RUNBOOK.md). This procedure does not mean a customer release is ready.

## Person 1 quick start

```powershell
uv sync --project person2_platform --python 3.12 --locked --all-extras --dev
$env:PYTHONPATH = "evals"
person2_platform/.venv/Scripts/python.exe -m pytest packages evals -q --basetemp=.pytest-person1
person2_platform/.venv/Scripts/python.exe evals/run_evals.py --provider-mode mock
```

Person 1 is installed as `finledger-ai`, including its prompts and schemas, in
both dependency environments and the runtime image. The ingest, extract, score and map workers
(`finledger-platform worker`, `extract-worker`, `score-worker` and `map-worker`) run locally with the deterministic
mock provider. An opt-in Gemini path (`FINLEDGER_GEMINI_LIVE=1` plus explicit settings) exists but is off, has never
been run and is not approved. Staging and production reject mock AI, and live use needs the owner's provider,
location, retention and budget decisions.

## Runtime order

```text
PostgreSQL → migrations/bootstrap → Person 2 API (upload, intake) and ingest worker
           → extract, score and map workers (mock provider locally)
           → Person 3 control server serving the desk (review, correction, approval)
           → Person 4 connector (separately maintained; outside this handoff and not used by the desk MVP)
```
