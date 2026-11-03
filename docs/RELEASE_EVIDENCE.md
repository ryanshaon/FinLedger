# Release evidence — 2026-11-04 desktop MVP, local verification

Branch `fix/desktop-mvp-verification`. Base `d38bbe0fe261f794c41fc84ecc5ac46dce647feb` (`d38bbe0`), which
was also `origin/main` with no open pull requests when last fetched on 2026-11-04. Implementation commit: `3acce09215f5b95b3c267b38320db92eaa9f1c41`.
Documentation and subsequent UI improvements are tracked on the same branch.
The test runs below were made on 2026-11-02 and 2026-11-03 on that tree (Windows, Python 3.12, a throwaway
PostgreSQL 18 cluster started outside the restricted runner, Node 24.15.0) and were **not rerun on 2026-11-04**.
GitHub CI `test`, `image` and `desk` passed on implementation commit `3acce09`:
[CI run](https://github.com/ryanshaon/FinLedger/actions/runs/41698738740). Final branch head must pass again before merge.
These local runs are not an independent security review and are not a deployment or customer-readiness claim.

No Supabase SQL, configuration or key, hosted Auth, S3, scanner, email, model provider or hosting was touched or
contacted. No secret was read or printed. `person4_tally/` and migrations `001`–`015` were not edited. Docker is not
installed on this machine; GitHub CI built and verified the image. `--live-synthetic-eval` was not run.

## Results

| Check | Result |
|---|---|
| `packages` + `evals` pytest | **257 passed** |
| `person2_platform/tests` | **378 passed**, 1 existing Starlette warning |
| `person3_control_ui/tests` | **112 passed**, same warning |
| `evals/run_evals.py --provider-mode mock` | **3/3 structural, 6/6 exact** |
| `desk`: `npm test` | **4 passed** |
| `desk`: `npm run build` | **passed** (`tsc --noEmit && vite build`) |
| `desk`: `npm run test:browser` | **8 passed**, 0 skipped (Chromium against a synthetic, intercepted API) |
| `git diff --check` | clean |
| `docker build -t finledger:ci .` | **not run** (no Docker) |
| `evals/run_evals.py --live-synthetic-eval` | **not run** |

The Python commands are the ones in [HANDOVER.md](../HANDOVER.md) "How to verify". One Windows-only test, the refused-connection
HTTP-client check, timed out at 1 s: measured 1 s gives `TimeoutError` and 3 s gives `ConnectionRefusedError` after about 2.03 s, so the test
timeout is now 3 s. No production transport code changed.

## What the tree shows locally

- **Upload boundary** (`person3_control_ui/tests/test_mvp_upload.py`): a clerk or firm admin can upload one raw PDF, JPG or PNG; the original
  bytes and hash are stored, the document is `received` with `virus_ok` null and an `ingest` job is written. Approvers and payers get 403, as do
  a wrong CSRF token, a foreign origin and a foreign company; an oversized body (declared or streamed) gets 413 and a non-invoice type 422, with nothing saved.
- **Whole journey** (`test_staff_upload_all_consumers_correction_and_second_reviewer` in `person3_control_ui/tests/test_local_journey.py`): staff upload
  → real ingest, extract (mock provider), score and map consumers → `in_review` with an original link → clerk saves a party-ledger
  correction → the clerk's own approval is 403 → a second reviewer approves with `post` false → zero `post` jobs and one approval →
  a second upload with a different invoice number retrieves the saved correction as its party ledger.
- **Original and approval record**: the stored bytes equal the uploaded bytes. The original link is signed only after the virus scan passed and never
  for a quarantined bill. A bill that is received but not yet mapped opens as an empty review with edit and approve disabled.
- **Legacy HTML review fallback**: review found it returned 200 for such a bill; it now returns 404 (the JSON source-only view still returns 200).
- **Retrieval**: a memory whose known vendor GSTIN conflicts with the bill is dropped before top-k; the missing-GSTIN fallback is kept; equally
  scored memories rank newest first through indexed `created_at`. The conflict, recency and top-k crowding regressions failed before the fix and pass after. No model is retrained.
- **Desk** (`desk/tests/mvp.browser.test.cjs`): upload headers and bytes, role gating, client-side size and type limits, a late upload
  that cannot navigate after a company switch, tax fields and passed / failed / not-checked marks, approval sends `post: false`, a revision conflict
  reloads the latest bill, and unprocessed bills expose no edit or approval controls. Posted, Transactions, outstanding amounts, TDS and three-way panels are no longer on the primary screens.
- **Packaging** (executed successfully in GitHub CI on `3acce09`): the `Dockerfile` has a Node 24 `desk-build` stage that runs `npm test` and `npm run build` and copies `dist` into the final image;
  `ci.yml` has a `desk` job (Node 24, helper tests, build, Chromium with `playwright-core` pinned to 1.62.1) and an image check that `dist` exists.

The provider is a deterministic mock and the scanner an EICAR stub, so none of this is real OCR or ClamAV behaviour. The browser checks do not talk to the real server.

## Known limits (found in review, not fixed here)

- Until the ingest worker's scan sets `virus_ok`, the original is stored but cannot be opened, by design.
- The staff upload route has no per-user rate limit (the public portal has one) and holds up to about 50 MiB per request.
- Uploading the same file twice creates two documents; duplicate detection happens only after extraction.
- File type detection accepts `%PDF-` anywhere in the first 1 KiB, so a polyglot file can be stored as PDF. The scanner is the control.
- `scripts/test.ps1` does not run the desk checks.

## Not done — owners and blockers

Everything hosted stays open. A green local run is not customer readiness.

| Item | Owner |
|---|---|
| Approve provider, model, location, retention, budget and capacity; supply private credentials; final acceptance | Ryan (`RYAN_WORK.md`). Not approved; the live switch stays off |
| Restricted app and worker logins, S3 smoke, Auth settings, real ClamAV, workers running, hosting, email | Harshith (`HARSHITH_WORK.md`). Open |
| Real staging sign-in, upload-to-approval walk-through, live synthetic eval | Ruthvik (`RUTHVIK_WORK.md`), after the two rows above. Not run |
| Green CI `test`, `image` and `desk` on the exact pull request head | Passed on `3acce09`; required again on final head before merge |

## 2026-11-04 usability follow-up: design, implementation and checks

Chosen approach: preserve React/Tailwind/Shadcn, search only assigned companies, filter only loaded queue
metadata. A new global search store or command palette would add needless state. Implementation order:
shared filter regressions, summary loading, dashboard/company/queue controls, keyboard navigation, then
browser/build and visual verification. All selected improvements are implemented.

- Header company/GSTIN search supports Enter, arrows and Escape and clears on navigation.
- Dashboard separates review/new/approved/exception counts with review value, refresh time and company
  queue shortcuts. Existing per-queue counts reduce summary requests from four to one per company.
- Company and bill filters have clear/no-match states. Queue tabs show counts. Complete and partial
  failures have refresh/retry, retaining available data while marking missing counts rather than showing zero.
- Keyboard skip link and focusable main landmark; dashboard, Clients and queue do not overflow at 390px.
  Main text contrast pairs: 11.59:1, 5.60:1, 7.99:1, 5.69:1. Screen-reader testing not performed.
- Packaged PNG routes fixed with an observed 404-before/200-after regression; unrelated files stay denied.
  Existing mark used as favicon; browser fixtures serve real PNG assets. Light/dark dashboard and invoice
  screenshots inspected locally using synthetic data.
- New search/count regressions failed against the old UI; the original eight browser flows passed.
  Final results: **8 Node helpers**, **14 Chromium tests**, **production build passed**; full control suite
  **113 passed**, one existing Starlette warning. AI/platform unchanged from verified 257/378 baseline.

Commands from the repository root (PowerShell):

```powershell
$env:FINLEDGER_TEST_OWNER_DSN = $null
$env:PYTHONPATH = 'person2_platform/src;person3_control_ui/src'
person3_control_ui/.venv/Scripts/python.exe -m pytest person3_control_ui/tests -q --tb=short --basetemp=.pytest-mvp-ui-final
npm --prefix desk test
npm --prefix desk run build
npm --prefix desk run test:browser
```

[PR #54](https://github.com/ryanshaon/FinLedger/pull/54) records final-head CI and integration. Initial implementation
`3acce09` passed all three CI jobs; require the updated head to pass again before merge. No hosted changes.

The first follow-up CI run passed Docker but caught a browser assertion racing React's route-change effect:
the URL had changed while the search input still briefly held its old value. The regression now waits for the
actual input to clear before asserting; no fixed delay or weaker assertion was added. Final CI must pass again.

## Rollback

Revert the pull request. No migration or hosted configuration changed.

---

Earlier report from 2026-10-27, kept unchanged below.

# Release evidence — 2026-10-27 local journey

Branch `feat/local-journey-proof`. Base is `b8cf3a37e221f24cec8b6b9402e434ad005e5b8f` (`b8cf3a3`).
Behavior commit these commands ran against: `e58901872aef26f66bd3771ae4ec0cdb8292b2ed`.
That commit is the local upload-to-review suite and the opt-in live Gemini switch.
This file is documentation on top of that commit and does not change runtime behavior.
The pull request head is the commit that contains this report.
CI `test` and `image` on the exact head are still required before merge. These local runs are
not an independent security review.

`b8cf3a3` already had migration `014`, `score-worker`, and `default_extract(conn)` durable
wiring. This run does not redo them.

Environment: Linux 6.12.94+ x86_64, Python 3.12.3, uv 0.13.6.
`initdb` was not on the default `PATH`. It was available as PostgreSQL 16.15 at
`/usr/lib/postgresql/16/bin/initdb`, and the suites ran with that directory on `PATH`.
No `FINLEDGER_TEST_OWNER_DSN` was set, so platform and control-UI tests booted a throwaway
cluster. No hosted database, Auth, S3, Resend, or model endpoint was contacted. No secrets
were printed. `person4_tally/` was not edited. Migrations `001`–`014` were not edited.
Nothing was applied to hosted SQL. `--live-synthetic-eval` was not run.

## Commands and results

`uv sync --project person2_platform --python 3.12 --locked --all-extras --dev` and
`uv sync --project person3_control_ui --python 3.12 --locked --dev` completed before the run.

| Command | `e589018` |
|---|---|
| `PYTHONPATH=evals person2_platform/.venv/bin/python -m pytest packages evals -q --basetemp=/tmp/pytest-person1b` | **176 passed**, 0 failed, 0 skipped |
| `PYTHONPATH=evals person2_platform/.venv/bin/python evals/run_evals.py --provider-mode mock --report /tmp/pytest-person1b/eval_report.md` | **3/3 structural, 6/6 exact** |
| `PYTHONPATH=person2_platform/src person2_platform/.venv/bin/python -m pytest person2_platform/tests -q --basetemp=/tmp/pytest-person2` | **372 passed**, 0 failed, 0 skipped, 1 existing Starlette warning |
| `PYTHONPATH=person2_platform/src:person3_control_ui/src person3_control_ui/.venv/bin/python -m pytest person3_control_ui/tests -q --basetemp=/tmp/pytest-person3` | **78 passed**, 0 failed, 0 skipped, same warning |
| `docker build -t finledger:ci .` | **skipped** — no Docker CLI. The PR `image` job is still required |
| `evals/run_evals.py --live-synthetic-eval` | **not run** |

`git diff --check` was clean on the behavior tree. The mock report was written under `/tmp`,
so `evals/latest_report.md` was not rewritten. `docs/assignments/` still contains exactly
`RYAN_WORK.md`, `HARSHITH_WORK.md`, and `RUTHVIK_WORK.md`.

The platform and UI commands above ran on the same behavior tree. The packages command was
repeated after the last behavior edit and is the count in the table.

## What this tree proves

- Upload of a synthetic text-layer invoice, ingest, `default_extract(conn)` with the mock
  provider, and `default_score()` reach `scored`. A clerk correction of `party_ledger` and a
  different human's draft approval reach `approved`. The clerk who edited cannot approve.
  On `received`, the desk says `Queued for extraction`, not a success. After scoring it says
  `Scored`.
- Replaying the same extract job does not call the model again, does not add a usage row, and
  does not enqueue a second score job. Replaying the score job does not add a risk assessment
  or bump the canonical revision. Replaying both after approval does not add a correction or
  an approval.
- A lost extract lease leaves the document `received` and writes no score job. A lost score
  lease leaves it `extracted` and writes no canonical invoice or risk row.
- A schema-invalid invoice (extract succeeds, Person 3's boundary rejects it) becomes
  `canonical_invoice_rejected`. The score job is `done`. Another claim does not retry it.
  A manual requeue is `skipped` and still writes no canonical row.
- A scanner outage (`clamd` on a closed port) leaves `virus_ok` null, status `received`, and
  no extract job, with `last_error` `virus scanner unavailable`. Restoring the dev scanner
  retries the same job to `prepared`.
- A per-bill cap of 1 becomes `ai_budget_exceeded`, the extract job is `done`, the model body
  is not generated, and the desk says `AI budget exhausted`.
- Provider timeouts (`provider_transient`) retry and the extract job is `dead` after 5
  attempts. The document stays `received`. The desk says `Extraction failed`.
- A statement never calls the model and becomes `statement_not_extracted` (`Statement excluded`
  on the desk). An object key outside `{client_id}/` never calls the model and becomes
  `object_key_rejected`.

## Opt-in live Gemini — present, not run

`ModelRouter` still refuses `mock` in `staging` and `production`. It refuses `gemini` there
unless the transport is an `HttpGeminiTransport` whose client is a `GeminiHttpClient`. It does
not construct that client. `default_extract` builds one only when `FINLEDGER_GEMINI_LIVE=1`.
That switch also requires `FINLEDGER_LLM_PROVIDER=gemini` and explicit
`FINLEDGER_MODEL_EXTRACT`, `FINLEDGER_GEMINI_LOCATION`, `FINLEDGER_GEMINI_PROJECT`, and
`FINLEDGER_GEMINI_CREDENTIAL`. A missing value fails before a socket. There is no mock, region,
or model fallback. The credential is not written into errors.

`evals/run_evals.py --live-synthetic-eval --provider-mode gemini` is the separate command.
It is off unless passed. CI still runs `--provider-mode mock` only. This run did not pass the
live flag and did not set a credential.

## Not done — owners and blockers

Hosted checklist items stay open. Local CI is not customer readiness.

| Item | Owner |
|---|---|
| Approve provider, model, processing location, retention, budget, capacity, and any tariff | Ryan (`RYAN_WORK.md`). Not approved. The live switch stays off |
| Private credentials, kept out of Git and chat | Ryan |
| Final acceptance and any customer-launch decision | Ryan. Local CI is not that acceptance |
| Hosted apply, app login, storage, scanner, email, deploy, and the staging URL | Harshith. Open |
| Run `--live-synthetic-eval` on synthetic or redacted invoices and record model, location, tokens, and cost | Ruthvik, only after Ryan's approval and an account you control. Not this run |
| Real synthetic-staff invite → password → login → TOTP → client list → logout, plus AAL1, cookie, expiry, and cross-tenant checks | Ruthvik, after Harshith's staging runtime and Ryan's Auth settings. The fake Auth test is not this proof |
| Green CI `test` and `image` on the exact pull request head | Required before merge. Not claimed here |

## Rollback

Revert the pull request. There is no migration to roll back and no hosted configuration to restore.
