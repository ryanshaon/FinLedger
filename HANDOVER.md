# FinLedger handover (shared by ChatGPT/Codex and Claude)

This file is the single baton between AI agents working on FinLedger. **Read all of it before touching code.**
Whoever is working updates the "Latest status" section and pushes it before stopping. It replaces the old
`HANDOVER_CLAUDE.md`.

---

## 🔁 Hand-back protocol (MANDATORY, read first)

When your credits/usage are running low (**owner's updated threshold: start at ~5% remaining, not at 0%**):

1. **Stop starting new work.** Finish or cleanly park the current change.
2. **Commit everything.** Nothing may stay uncommitted on the Windows machine. The last handover lost
   migration 008 and the JWT verifier this way and they had to be recovered from Supabase and a WIP branch.
   - Finished and tested work: branch `handover/<short-topic>`, push, open a PR.
   - Unfinished work: commit anyway on `wip/<short-topic>` with a commit message that starts with `WIP:` and push it.
     Failing tests are OK on a `wip/` branch if the commit message lists them.
3. **Update the "Latest status" section of this file** (in the same push): what you did, what's verified (exact
   test counts / commands), what's half-done, which branch it's on, anything you applied to Supabase staging.
4. **Never apply SQL to Supabase staging without also committing the same SQL file** in
   `person2_platform/src/finledger_platform/migrations/` in the same session.
5. Tell the user, in one message: "Handing back to Claude. Read HANDOVER.md on branch `<branch>`." Then stop.

Claude does the same in reverse when handing to you.

---

## Mission and hard rules

- Product: a sellable multi-tenant accounts-payable app for Indian CA firms. Persons 1–3 only.
  **Never edit `person4_tally/`.**
- Never print, commit or paste secrets. Real values live only in environment/secret settings. `.env` is gitignored.
- Never run the app as `postgres`/service role/any RLS-bypassing role. App role = login that is a member of
  `finledger_app` (the code refuses others via `assert_rls_applies`).
- Never claim "production ready" while open items below remain.
- Be explicit about what is live, local-only, untested, or blocked.
- **Owner rule (hard): no AI attribution in Git.** Never add `Co-authored-by`, `Claude-Session`, "Generated with …"
  lines, or an AI author identity (e.g. `Claude <noreply@anthropic.com>`) to commits, PR titles/bodies or comments.
  Commit as the owner (`git config user.name ryanshaon`,
  `git config user.email 250547248+ryanshaon@users.noreply.github.com`). Check `git log -1 --format='%an <%ae>%n%b'`
  before every push.
  GitHub squash defaults can change the author email and append owner co-author trailers. For future merges,
  pass `--author-email 250547248+ryanshaon@users.noreply.github.com` and a reviewed `--body-file` explicitly,
  then inspect the published commit as well. Never silently rewrite shared history to repair metadata.
  The GitHub squash endpoint rejected that noreply override on PR #20. A safe alternative used for PRs
  #20/#21 is a **non-forced fast-forward** of the exact reviewed branch after all PR checks pass; this
  preserves the already-verified commit identity/message. Abort if it is not a fast-forward. GitHub
  subsequently marks the PR merged. Never use a force push as a routine PR integration method.

## Repository

- **Current assignments:** exactly three files under `docs/assignments/`:
  [Ryan](docs/assignments/RYAN_WORK.md), [Harshith](docs/assignments/HARSHITH_WORK.md),
  [Ruthvik](docs/assignments/RUTHVIK_WORK.md). Update these in place, replacing superseded
  task lists. Older filenames in dated history below are historical, not active instructions.

- **Three-contributor onboarding:** [TEAM_WORK_SPLIT.md](docs/TEAM_WORK_SPLIT.md) assigns AI,
  platform/deployment and controls/QA ownership, dependencies and acceptance checks. Use it for friends
  joining the project; this handover remains authoritative for decisions and hosted state.

- GitHub: `ryanshaon/FinLedger`. Default branch `main` (protected by habit: changes go via PR, CI must be green).
- Pass `--repo ryanshaon/FinLedger` to `gh` commands: this fork's CLI default may select the upstream repository.
- Windows checkout: `C:\Users\ryans\OneDrive\Documents\Studies\Projects\FinTechShi\FinLedger_GitHub`.
- Layout: `packages/`, `evals/`, `person1_ai_rag/` (Person 1 AI/RAG) · `person2_platform/` (API, tenancy, DB,
  workers, storage, auth) · `person3_control_ui/` (controls, workflow, review UI) · `person4_tally/` (out of scope).

## How to verify (same commands as CI, `.github/workflows/ci.yml`)

```bash
uv sync --project person2_platform --python 3.12 --locked --all-extras --dev
uv sync --project person3_control_ui --python 3.12 --locked --dev
PYTHONPATH=evals person2_platform/.venv/bin/python -m pytest packages evals -q
PYTHONPATH=evals person2_platform/.venv/bin/python evals/run_evals.py --provider-mode mock
PYTHONPATH=person2_platform/src person2_platform/.venv/bin/python -m pytest person2_platform/tests -q
PYTHONPATH=person2_platform/src:person3_control_ui/src person3_control_ui/.venv/bin/python -m pytest person3_control_ui/tests -q
docker build -t finledger:ci .        # CI "image" job
( cd desk && npm ci && npm test && npm run build )         # CI "desk" job
( cd desk && npx --no-install playwright-core install --with-deps chromium && npm run test:browser )
```

Verified local baseline: packages + evals **257 passed**, platform **378 passed** (2026-11-02/03),
mock evals **3/3 + 6/6**. Updated 2026-11-04: complete control suite **113 passed**, desk Node helpers
**8 passed**, Chromium **14 passed** (synthetic API), production build passed. Existing Starlette warning remains.
Initial implementation `3acce09` passed all CI jobs (`test`, `image`, `desk`, run `41698738740`).
Final-head checks and integration are recorded on [PR #54](https://github.com/ryanshaon/FinLedger/pull/54).
Re-run affected checks after behavior changes and require exact-head green CI before merging.
- Cloud containers: PostgreSQL may be installed but not on PATH. Use
  `export PATH=/usr/lib/postgresql/16/bin:$PATH`.

- DB tests start a throwaway PostgreSQL via `initdb` (needs PostgreSQL on PATH). As root they run it as the
  `postgres` OS user automatically. On Windows the sandbox used to stall `initdb`: prefer CI or WSL.
- `run_evals.py` rewrites the timestamp in `evals/latest_report.md`; avoid running it just for a handover. If you
  run it, either include the updated report intentionally or restore only that generated file after checking the
  diff. Do not discard unrelated work.
- If you change `person2_platform/pyproject.toml` dependencies, run `uv lock` in **both** `person2_platform` and
  `person3_control_ui` (Person 3 depends on Person 2), then `uv lock --check` both.

## Supabase staging (current project `smnfnnwmasnsuuwrboge`, `ap-south-1`, no customer data)

**Current as of 2026-11-02:** owner replaced the paused project with **StartUps / FinLedger**.
Connected account label = **Startups**; tool calls must explicitly select that account's `link_id`,
not Primary. New API URL: `https://smnfnnwmasnsuuwrboge.supabase.co`.
The October 15 backup's empty app baseline and private 25 MiB bucket were restored, then
committed migrations 012-015 applied. **001-015 are now recorded in `public.schema_migrations`;
do not replay them.** Supabase's history records two recovery batches. All 30 application tables
have RLS enabled; API roles have no direct application table/function grants; rollback-only AI/tenant
proof **11/11** passed and cleanup was confirmed. Security advisor: only the three expected INFOs.
Document UPDATE/DELETE isolation remains unverified here (connector confirmation expired).
No runtime login/password, S3/Auth keys, hosted Auth settings or `.env` connection was migrated.
See [recovery evidence and exact batches](docs/SUPABASE_RECOVERY.md) before any further hosted work.

### Historical old project (`hxymklwqifwojziewtcv`): not the current destination

State verified 2026-10-09:
- Both Supabase's migration history and `public.schema_migrations` include `001`–`011`. Migrations 009, 010 and
  011 were applied from the exact committed files after green PRs #7, #9 and #10. **Do not re-apply them.**
- `finledger_private.staff_sessions` and `staff_session_events` have RLS enabled and no direct SELECT grants to
  `anon`, `authenticated` or `finledger_app`; all have zero rows. Security advisor only reports intentional INFO
  “RLS enabled, no policy” for these two tables and `schema_migrations`.
- 24 public tables, all RLS on; `anon`/`authenticated`/`service_role` have no table grants (006).
- Bucket `finledger-documents-dev`: private, 25 MiB limit, 0 policies (intentional: app uses S3 keys), 0 objects.
- Firms/clients/vendors: 0 rows.
- Hosted RLS proof (rollback-only) passed under `finledger_app`:
  - no tenant context → 0 rows
  - client A sees only itself
  - A cannot insert into B
  - firm 2 sees only its own client
  - Cross-tenant UPDATE/DELETE were proven in an earlier run and locally; via the Supabase MCP connector,
    statements containing UPDATE/DELETE hang until timeout (likely a hidden approval gate), so run those
    through `finledger-platform isolation-check` with the owner DSN instead.

Gotchas:
- On Supabase, `postgres` is an ADMIN member of `finledger_app` but **without SET**. To `set role finledger_app`
  in a test transaction you must `grant finledger_app to postgres with set true` inside that same transaction
  (rolled back with it). `isolation-check` already does this.
- No app login role (e.g. `fl_app`) exists on staging yet; none has been created with a real password.

## What exists (all on `main`)

| Area | Where | State |
|---|---|---|
| Tenancy + RLS, queue, intake, storage, outbox worker | `person2_platform` | tested |
| Supabase hardening 006/007 | migrations | applied on staging |
| Auth subject link 008 | `migrations/008_supabase_auth_subject.sql` | applied on staging. Rule: **never auto-link by email/JWT metadata**; admin links explicitly |
| Supabase JWT verifier + Auth/session backend | `finledger_platform/supabase_auth.py`, `auth_gateway.py`, `browser_sessions.py` | tested offline and with local Postgres; PRs #7–#9 merged. Browser routes are on `main`, not deployed to staging |
| Staging proof tools | `finledger-platform isolation-check`, `finledger-platform s3-smoke` | tested; s3-smoke never run against real Supabase (no valid keys yet) |
| Runtime image + local stack | `Dockerfile`, `compose.yaml` | image includes installed `finledger-ai` and built desk; CI verifies imports, assets and hosted mock guards. Ingest, extract, score and map workers are wired for local mock verification; hosted activation remains gated |
| Control UI entry point | `finledger-control` | uses `make_pool` (RLS guard) and full S3 settings |
| Staff invites + first login | `finledger_platform/staff_invites.py`, Person 3 `/admin/staff`, `/auth/accept` | merged in PR #15; tested offline with fake Auth only. Disabled until `SUPABASE_SECRET_KEY` is set |
| Worker diagnostic privacy | `worker.py`, `outbox_worker.py`, `tests/test_worker_diagnostics.py` | PR #18: fixed failure categories, no exception payload in worker-owned logs or standard escaping tracebacks; retry/rollback/lease recovery tested |
| Auth design | `docs/plans/AUTH_SUPABASE_DESIGN.md` | implemented except staging Auth settings, hosting and real-user E2E |
| Desktop MVP (`desk/`, `person3_control_ui/.../desk_api.py`) | staff upload, review checks, second-reviewer approval, company-scoped retrieval | on branch `fix/desktop-mvp-verification`, implementation `3acce09`; tested locally with a mock provider and synthetic API; not deployed |

## ✅ Owner checklist (things only the owner can do)

Tick these off in order. An agent can help explain each step but must never see or type the secret values.

1. **Windows checkout aligned (completed 2026-10-09).** This checkout now follows rewritten `origin/main`;
   the pre-rewrite history is preserved locally at `archive/pre-history-rewrite-20261009` and was not pushed.
   Do not reset again just to repeat this checklist. For any other older clone, preserve local work before
   aligning it with rewritten `origin/main`.
2. **Optional cleanup on GitHub:** delete merged branches `claude/cloud-session-h52oob`, `claude/lucid-faraday-oruuq9`
   and `handover/*`. They still hold pre-rewrite commits.
3. **Windows `.env` for S3:** copy `.env.example` only if `.env` does not exist, then open `.env` yourself;
   never overwrite existing credentials with the example. Set `FINLEDGER_STORE=s3`,
   `FINLEDGER_S3_BUCKET=finledger-documents-dev`,
   `FINLEDGER_S3_ENDPOINT_URL=https://smnfnnwmasnsuuwrboge.supabase.co/storage/v1/s3`,
   `FINLEDGER_S3_REGION=ap-south-1`, `FINLEDGER_S3_ADDRESSING_STYLE=path`, and **type** `AWS_ACCESS_KEY_ID` /
   `AWS_SECRET_ACCESS_KEY` yourself. `.env` is gitignored; never paste the keys into chat. Then run
   `finledger-platform s3-smoke` (open item 1).
4. **Supabase invite setup** (before any real invite), from `AUTH_SUPABASE_DESIGN.md` §3.4:
   - put `SUPABASE_SECRET_KEY` only in the control UI server's secret settings;
   - Supabase → Auth → Email templates → *Invite user*: link =
     `{{ .SiteURL }}/auth/accept?token_hash={{ .TokenHash }}&type=invite`;
   - set Site URL + redirect allow-list to the control UI origin; turn public sign-ups **off**;
   - access-token expiry 900 s; enable TOTP MFA; leaked-password protection on.
5. **Approve the remaining AI provider/data policy** (see [ADR-001](docs/decisions/ADR-001-AI_PROVIDER.md)):
   - a. **Decided 2026-10-10:** owner delegated the better choice; use one non-login "FinLedger automation"
     maker per firm, with no Auth subject, token or firm-admin privilege. Not yet implemented.
   - b. **Pending approval:** provider, processing jurisdiction, permitted invoice text/images, retention/logging
     exceptions, budget and capacity. Owner requested research, not provider activation. ADR-001 is proposed.
     **Follow-up:** minimized invoice text and bounded page images are permitted under the proposed controls;
     serve Indian and EU markets, but decide processing locations later. Budget remains undecided. Do not
     infer either cross-border permission, country-local routing, a capacity purchase or live-model activation.
   - c. **Decided 2026-10-10:** owner delegated the better choice; exclude bank/account statements from v1
     extraction and handle them as an explicit exception. Not yet implemented in the consumer.
6. **Choose a password for the staging app login role** (open item 3) and store it only in secret settings.
7. **Email:** verify a sender domain in Resend and store the API key in secret settings (open item 4).
8. **Hosting:** approve an account and budget (open item 5).

## Open work, in priority order

| # | Item | State | Blocked on |
|---|---|---|---|
| 0 | **Desktop MVP delivery:** commit `fix/desktop-mvp-verification`, push, open the PR, exact-head CI `test`, `image` and `desk` | implementation and UI follow-up complete locally; PR #54 carries final-head CI and integration status | none; no hosted changes |
| 1 | **S3 smoke test against Supabase** (`FINLEDGER_STORE=s3 … finledger-platform s3-smoke`) | code ready, never run live | owner checklist 3 |
| 2 | **Sign-in staging E2E** (Playwright: invite → set password → login → TOTP → client list) | all code merged (PRs #7–#9, #15); migrations 009–011 applied, **never re-apply** | owner checklist 4 + hosting |
| 3 | **App login role on staging** (`fl_app`-style, member of `finledger_app` + worker roles, via the Supabase **session pooler**) | not created | owner checklist 6 |
| 4 | **Email delivery** (Resend outbox implemented; inbound webhook untested) | code ready | owner checklist 7 |
| 5 | **Hosting** (recommendation: AWS ECS/Fargate Mumbai next to Supabase `ap-south-1`; Render is the simple alternative) | image ready | owner checklist 8 |
| 6 | **Person 1 release blockers:** live provider activation, PII policy, real-document extraction proof (the extract, score and map consumers and durable usage/RAG state exist and run locally with the mock provider) | opt-in Gemini path present, never run | owner checklist 5 (except 6a below) |
| 6a | **Package Person 1 into the runtime image** | implemented and verified in PR #17 | none; no provider or consumer added |
| 6b | **Worker diagnostic sanitization** | implemented and verified in PR #18 | none; no hosted changes |
| 6c | **Malformed control form inputs** | PR #21 integrated with green CI; four regression cases | none; no hosted changes |
| 6d | **Browser cookie MFA assurance binding** | PR #22 integrated after independent review and green CI | none; no migration or hosted changes |
| 6e | **Hosted antivirus configuration guard** | PR #25 integrated after green CI | none; no migration or hosted changes |
| 6f | **Credential URL access-log privacy** | PR #26 integrated after green CI | none; proxy logging remains operator configuration |
| 6g | **Signed-download validation and safe filenames** | PR #27 integrated after independent review and green CI | none; local-store boundary only |
| 6h | **Storage smoke failure/report accuracy** | PR #27 integrated after independent review and green CI | no keys needed for code tests; live smoke still blocked |
| 6i | **Strict bounded scanner response validation** | PR #28 integrated after independent review and green CI | none; no migration or hosted changes |
| 6j | **Email webhook request boundary** | PR #29 integrated after independent review and green CI | none; live email remains blocked |
| 6k | **Aggregate scanner deadline and post-auth email DB acquisition** | PR #30 integrated after independent reviews and green CI | none; no hosted changes |
| 7 | **Performance:** 28 unindexed FKs, 13 "unused" indexes on an empty DB | do not act on zero-data advisor stats | real query plans |

Owner decisions already answered (do not re-ask): password + TOTP MFA; SSO deferred; 8 h absolute / 30 min idle
browser sessions, applied to all staff with AAL2 required before client data.
Also decided: per-firm non-login automation maker and statement exclusion for v1. These two choices do not
approve a live provider, invoice transfer, paid resources or automatic posting/payment.

## ▶️ Next agent task (resume the first owner-unblocked item)

**Packaging (PR #17), worker diagnostic sanitization (PR #18), form input hardening (PR #21) and browser MFA
cookie assurance (PR #22), scanner configuration/logging guards (PRs #25/#26), signed downloads/storage
proof reports (PR #27), strict scanner protocol validation (PR #28), email request limits (PR #29),
and scanner deadlines/post-auth email pool acquisition (PR #30)
are implemented and verified. Do not repeat them.** The remaining Open work items need credentials, configuration, an approved hosting account/budget,
owner policy decisions, or real query plans. Start from current `main` and inspect the linked PRs before editing.

**Desktop MVP delivery (first; needs no hosted access):** branch `fix/desktop-mvp-verification` carries the desktop MVP,
implementation `3acce09` on `d38bbe0` plus the completed usability follow-up.
[PR #54](https://github.com/ryanshaon/FinLedger/pull/54) is the delivery record. Before integration require exact-head
green CI `test`, `image` and `desk`; after integration resume only the owner-unblocked hosted gates below. Apply no SQL, change nothing in Supabase, enable no provider
and add no paid resource as part of it. Then continue with the numbered items below.

**Metadata correction completed with explicit owner approval:** PR #19 had green `test` and `image` checks
on `f7cc5ac`. An exact-lease push replaced the expected `03ec760` remote tip with that reviewed tip.
Replacement commits `d77b124` and `12c93c1` preserve the original application trees and dates, with the
owner's noreply identity and no attribution trailers. GitHub automatically marked PR #19 merged.
The original tip remains on local `archive/pre-owner-metadata-correction-20261009`; do not push it.
Other clones must preserve local work before aligning with the updated history. Do not repeat this rewrite.

1. If the owner has entered real S3 keys privately, run Open work 1 with the private staging bucket. Do not print
   environment contents or secret values. Record actual smoke-test results and cleanup; never claim it was run
   from a configuration-presence check.
2. If the Auth settings, app login role, private server secrets and runtime are ready, run the real staging
   invite → password set → login → TOTP → client-list E2E. Continue to use the non-owner app role and explicit
   Auth-subject binding. Migrations 009–011 are already applied; do not re-apply them.
3. Maker and statement choices are recorded in checklist 5. Once its remaining provider/processing/data-policy
   and spend gates are explicitly approved, implement the adapter/accounting and extract consumer from the
   audit/ADR design and test lists on a new branch. Research authorization is not activation approval.
4. If none is unblocked, report the exact missing inputs rather than guessing policies, provisioning paid
   resources or claiming production readiness. The provider proposal is researched; ask only the remaining
   ADR approval questions, not the already-decided maker/statement questions.

Keep the owner's **5% remaining** usage hand-back threshold, update Latest status before stopping, and push
every completed or WIP change. No uncommitted implementation may remain on the PC.

---

## Latest status

**2026-11-06 — hand back to Claude at owner threshold:** Code and PR #66 are on
`hardening/jobs-write-boundary` at `c51e609`; this handover is on `wip/jobs-write-boundary`.
Exact-head image and desk CI passed; both test jobs were pending at handoff. Full local runs before
the last lock-timeout change were **389 platform / 121 control passed**; after that change the
complete control service file passed **21 tests**. Do not treat the earlier full-suite counts as
verification of the final head. Next: inspect PR #66 CI and fix any failures, review migration 016
and residual queued-post INSERT risk, then merge only with green exact-head CI. Migration 016 was
**not applied to Supabase**. The earlier committed hosted synthetic queue/RAG proof passed and
cleaned its fixture; only sequences may have advanced. No Person 4 edits or customer data changes.
After a reviewed merge, verify migration/application compatibility before applying it to staging;
the user has not selected a live AI provider or hosting budget. No credentials belong in Git.

**2026-11-06 — queue write boundary and edit race:** Branch `hardening/jobs-write-boundary`
prepares migration 016: remove direct app UPDATE/DELETE on jobs, restrict INSERT to producer
columns, and use tenant-checked definer functions to lock post jobs and cancel queued/dead
attempts during edits. Locks include dead jobs so a concurrent requeue cannot revive an old
approval; a 3-second lock timeout becomes a retryable conflict. Existing hosted queue-prepare
SQL is historical after 016; the mapping fixture no longer uses it. API roles are denied the
new functions. Before the final timeout change full local suites passed **389 platform / 121
control**; after it the complete service file passed **21 tests**. Final full-suite and CI
results must be checked separately. Claude reviewed the design; no Person 4 edits, hosted SQL,
or customer data changes in this batch. **Residual risk:** app SQL can still enqueue a forged
queued post job; no user-facing exploit has been demonstrated. Do not claim job forgery closed.
Migration 016 has not been applied to Supabase; apply only after exact-head green CI and review.

**2026-11-06 — hosted synthetic queue proof passed:** PR #64 merged with green CI. On
`smnfnnwmasnsuuwrboge`, the restricted `fl_map` login claimed and finished its fixed synthetic
map job within a forced rollback; a wrong token and an extract-queue claim were denied, and
original fixture lease/state was restored. Mapping, correction isolation, no-post and cleanup
passed again. Independent counts: **0 firms, clients, documents, RAG chunks, jobs, usage rows and
reservations**. Local synthetic intake-to-review journey **8 passed**; role/queue **10 passed**;
map fixture **5 passed**. This does not prove concurrent SKIP LOCKED, real intake/extraction,
live providers or Tally. Synthetic seed/cleanup and rolled-back queue update were the only hosted
SQL operations; sequences may have advanced. The tenant-scoped direct jobs write gap below remains.

**2026-11-06 — queue proof and permission review in progress:** Branch
`test/hosted-map-queue-claim` adds a rollback-only fixture-specific map claim/finish check and
isolated local role tests. The synthetic full intake-to-review journey passed **8 tests** locally;
queue role and existing queue tests passed **10 tests** locally; the mapping fixture suite passed
**5 tests** locally, including rollback and unrelated-claim rejection. The general `finledger_app` role
inherits direct write privileges on tenant `jobs` from migration 003, so a session with SQL access
can alter its own tenant's queue rows outside the worker functions. Hosted read-only checks confirmed
INSERT/UPDATE/DELETE grants and enabled RLS. This is a least-privilege gap,
not a demonstrated public-endpoint exploit. The control service currently updates queued post
jobs during cancellation; any hardening must preserve that path. No new hosted changes have been
made during this batch. Hosted queue proof, final CI and permission remediation remain pending.

**2026-11-05 — hosted synthetic mapping/RAG proof passed:** Ran the committed, exact-head
`scripts/hosted_map_proof.py` from PR #62 against replacement project `smnfnnwmasnsuuwrboge`
using the restricted `fl_map` session-pooler login and a process-local mock for synthetic invoices.
Both synthetic companies mapped; a previous-bill correction set only company A's party ledger,
company B remained isolated, no post job was created, and fixture cleanup passed. Independent
read-only counts afterward: **0 firms, clients, documents, RAG chunks, jobs, usage rows and token
reservations**. Sequences may have advanced. Four local PostgreSQL fixture/cleanup tests and all
PR #62 CI checks passed. The proof preclaims fixture-specific jobs and calls `process_map`; it does
not test shared queue claiming, intake, extraction, live AI, real Tally, or hosted deployment.

**2026-11-05 — synthetic hosted map proof preparation:** Branch
`test/hosted-map-retrieval-proof` adds fixed, operator-only synthetic seed/cleanup SQL and
a real `process_map` test across two client contexts. It preclaims only fixture jobs (no shared
queue claim), checks that a correction wins only for its own client and that no post job is
created. Local PostgreSQL: **4 passed**, including cleanup after an injected mapping failure,
refusal to clean up unexpected documents and rejection of an incorrect database target.
Hosted execution, exact-head CI and independent review are pending. A temporary development-mode
mock is for synthetic fixture invoices only; do not route real invoices to it. Seed commits rows
until cleanup and advances sequences. No hosted fixtures have been inserted yet.

**2026-11-05 — map login enabled:** Owner privately set the `fl_map` password. Verified only
that a SCRAM password exists (no password or hash read/displayed), then applied the committed
`recovery/enable_fl_map.sql` on `smnfnnwmasnsuuwrboge`. Verified login enabled, no superuser,
RLS bypass, role creation or database creation, and membership in `finledger_app` and
`finledger_worker_map`. Actual map-role session-pooler authentication and app RAG flow are
still untested; owner must supply its private connection URI. No customer data changed.

**2026-11-05 — map role created, login disabled:** Applied the committed operator-only
`recovery/provision_fl_map.sql` to `smnfnnwmasnsuuwrboge` after PR #59 passed CI and merged.
Verified `fl_map` cannot log in, is not superuser, cannot bypass RLS or create roles/databases,
and is a member of `finledger_app` and `finledger_worker_map`. It has no configured password.
Owner must set a new password privately via psql `\password fl_map`; only afterward run the
committed `recovery/enable_fl_map.sql`, then verify the non-owner session-pooler connection and
application RAG flow. Do not reapply the provisioning SQL. No customer data changed.

**2026-11-05 — restricted map-worker login preparation:** `setup/rag-runtime-login` adds
operator-only SQL to create `fl_map` without login, granting only `finledger_app` and
`finledger_worker_map`, plus a separate enable step after the owner sets a password privately.
One isolated PostgreSQL test passed. Hosted provisioning and private password setup are pending.
These operator scripts are not app migrations and contain no credentials.

**2026-11-05 — hosted RAG isolation proof passed:** Executed the committed
`recovery/verify_rag_isolation.sql` through the replacement project's owner session pooler.
All **8/8** assertions passed, including cross-client read/write/delete and upsert checks;
synthetic firm and temporary role removal were confirmed. Sequence values may have advanced.
No persistent schema or real customer data changed. This verifies SQL isolation, not the
application's hosted end-to-end retrieval. Restricted runtime logins are still not created.

**2026-11-05 — RAG proof in progress:** `test/hosted-rag-isolation` adds a rollback-only,
eight-assertion RAG tenant-isolation proof and one PostgreSQL test that checks both a secure
schema and a deliberately disabled RLS policy. Local test: **1 passed**. Hosted execution,
secure runtime login and end-to-end RAG remain pending. Synthetic proof rolls back rows and
role grants but can advance database sequences; it is not a schema migration.

**2026-11-05 — Supabase S3 smoke check:** User configured private S3 credentials locally. On branch
`fix/supabase-storage-denial`, the smoke test recognizes only the observed Supabase HTTP 400
`NoSuchBucket` response after a successful signed download; unrelated errors remain failures.
Focused tests: **16 passed**. Live synthetic upload, authenticated read, presigned download,
unsigned public denial and deletion all passed on `smnfnnwmasnsuuwrboge`. No SQL, provider
calls, customer data or Person 4 changes. The bucket was previously confirmed private and empty.
Database runtime login and hosted RAG proof remain outstanding.

**2026-11-04 approval replay authorization:** branch `fix/approval-replay-authorization` fixes an
approved-bill replay path that previously allowed a payer to request a posting job without approver
permission. The regression failed before the fix; the full local control suite passed **114 tests**
(one existing Starlette warning) afterward. No Supabase, migration, provider or Person 4 changes.
Check this branch's pull request and require green CI on its final commit before merging.

**2026-11-04 (Asia/Calcutta) desktop MVP and UI quality-of-life delivery:** branch
`fix/desktop-mvp-verification`, base `d38bbe0`, [PR #54](https://github.com/ryanshaon/FinLedger/pull/54).
Initial implementation `3acce09` is pushed with green CI `test`, `image` and `desk`
([run](https://github.com/ryanshaon/FinLedger/actions/runs/41698738740)). The follow-up completes the requested
usability pass and documentation. No half-written feature is intentionally parked. Consult PR #54 for the final
head's CI and integration state; merge only with green checks on that exact head.

- **Workflow:** bounded PDF/JPEG/PNG staff upload; AAL2, role, company and CSRF checks before streaming,
  membership/role rechecked before saving; original signed only after scanning; source/status before draft;
  tax and all risk marks; own-edit approval denied; desk approval always `post: false`. Local all-worker
  journey proves ingest/extract(mock)/score/map, independent approval without posting, and reuse of a saved
  party-ledger correction on a second invoice.
- **Retrieval:** reject known conflicting supplier GSTIN memory before top-k; preserve missing-GSTIN fallback;
  index correction `created_at` so newest equally relevant records win rather than arbitrary hash order.
- **UI:** company/GSTIN header search, company/bill filters, queue counts, dashboard workload shortcuts,
  honest metrics, timestamps and refresh/retry, partial-load warnings, keyboard skip link and narrow layouts.
  Company summaries use one queue request per company instead of four; unused duplicate loaders removed.
- **Packaging:** Node 24 builds the desk into the runtime image. Two allowlisted PNG routes serve brand
  images; unrelated files remain 404. Favicon added. The logo regression failed before the fix.
- **Verified:** packages/evals **257**, platform **378** (2026-11-02/03 baseline); full control **113**, Node
  helpers **8**, Chromium **14**, production build passed 2026-11-04. Mock evals **3/3 structural, 6/6 exact**.
  Control/platform retain one existing Starlette warning. Browser API is synthetic; worker tests use a
  deterministic provider and EICAR stub. Docker is absent locally; initial image build passed in CI.
- **Visual checks:** light/dark dashboard and invoice rendered and inspected. Main text pairs measure
  5.60–11.59:1 contrast; keyboard skip/search and 390px layouts tested. Screen readers were not tested.
- **Browser CI lesson:** URL changes precede React effect completion. Search-clear regressions wait for the
  input's actual empty value, not just the new URL; the first follow-up CI exposed this race.
- **Hosted changes:** none. No SQL/migrations, Supabase/Auth/storage settings, credentials, model calls,
  email or deployment changes. Person 4 is untouched; exactly three assignment files remain.

**Next work:** finish exact-head CI/integration for PR #54 if still open, then existing owner-gated runtime
login, Auth, storage, scanner, hosting and live-provider proofs. Do not repeat completed work or activate/buy
resources without recorded decisions. This is local/mock verification, not production readiness. The primary
MVP excludes email, finished every-bill TDS, PO/GRN/three-way screens, outstanding bills, Tally posting, bank
matching, payments and GST filing. Preserve future connector code. See [release evidence](docs/RELEASE_EVIDENCE.md).

**2026-11-02 replacement Supabase restored:** Branch `restore/new-supabase-finledger`, PR #53.
Owner supplied the October 15 private gzip backup and authorized the new StartUps project
`smnfnnwmasnsuuwrboge` in Mumbai. After reconnection, selecting account **Startups** explicitly
resolved the connector's missing-link error. Destination was empty. No raw backup, role password,
JWT/API key or secret value was printed or committed; no Person 4 files were edited.
Committed recovery SQL under `migrations/recovery/` before executing it; these operator-only files
are excluded from the non-recursive app migrator. Independent read-only SQL review found no
actionable issue. On reviewed `f179caa`, CI test/image passed: **254 AI / 378 platform / 101 UI**,
mock **3/3 + 6/6**; no local application suites rerun.

Hosted changes: batch `20261101231007 restore_backup_20261015_baseline` recreated the exact
001-011 application baseline and private empty bucket; table/column inventories, row counts,
23 policy names and six aggregate baseline checks all matched the backup. Batch
`20261101231038 upgrade_recovered_finledger_through_015` applied existing 012-015. App tracker
has all 15 filenames, 30 application tables all have RLS, and API table/function grants are zero.
Security advisors show only expected INFO notices on schema history and two private session tables.
The 17-check document-write proof hit an expired connector confirmation before execution; it is
not claimed as passed. The reduced committed proof at `ae0df45` passed **11/11** AI/tenant assertions;
synthetic firm, temporary role and result table cleanup were confirmed. See recovery evidence.

No replacement runtime credentials, `.env`, Auth settings, email, scanner, provider, deployed
service or paid resource was changed. Current task (database restore) is complete; connecting
the app still needs privately configured restricted runtime logins, new S3/Auth credentials and
the existing hosted proofs. Do not use postgres/service-role as an app connection. Old-project
pause/resume quota issue is not resolved by this restore. Finish with exact-head green PR CI
and push this status; no uncommitted work may remain.

**2026-10-24 Harshith local completion (local):** Branch `claude/project-thread-rzrjfr` from main `4bbf722`.
`extract-worker` now uses `PostgresKillSwitch` (injected into `ModelRouter`) and `PostgresUsageWriter` on the
pooled connection; a refused hold ends as document `exception` / `ai_budget_exceeded`. New migration
`014_llm_unpriced_usage.sql` makes `llm_usage_rows.cost_usd` nullable with no default, so an unpriced Gemini call
stores its tokens with NULL cost and its hold is reconciled. New `finledger-platform score-worker` runs Person 3
`ControlService.score` as the automation maker, in the same transaction as `finish_job`; login `fl_score` and a
compose service are documented. Migrations 001–013 unchanged. Suites (PostgreSQL 16): AI **165** / platform
**369** / UI **71**, mock **3/3 + 6/6**, 0 failed, 0 skipped. No hosted SQL, Auth, S3, email, or model call; Docker
not installed, so the image job was not run locally. Known limit: with `FINLEDGER_LLM_KILL_SWITCH=false` the
durable path has no real hold and settlement fails `unknown_reservation`; keep the switch on.

**2026-10-22 Ruthvik post-Harshith (local):** Branch `feat/ruthvik-post-harshith`,
[PR #7](https://github.com/jastiruthvik-web/FinLedger/pull/7). Behavior measured at
`614d47d87475ea611722bdb247e345cb7c490556`. Expired-hold choice is option 3: keep migration
013's rule (expired holds do not count; the 5× cap case is accepted; a timeout with no body
can stay uncounted). No edit to migrations 012 or 013, no migration 014, and `default_extract()`
is still the in-memory writer and router. Adapters settle a priced call on the real reservation
id; an unpriced Gemini success does not insert `cost_usd = 0`. Mock score payloads pass
`validate_invoice` with `extra="forbid"` kept. A human who drafted or corrected a bill cannot
approve it; the automation user stays the maker. Step 0 on `dbdf307` was AI **154** / platform
**360** / UI **64**, mock **3/3 + 6/6**. This commit's suites: AI **165** / platform **360** /
UI **69**, mock **3/3 + 6/6**, 0 failed, 0 skipped. No hosted SQL, Auth, S3, Resend, or model
call. Docker was not installed, so the image job was not run locally. Score-consumer ownership
is still not agreed. Evidence: [docs/RELEASE_EVIDENCE.md](docs/RELEASE_EVIDENCE.md). Handover
for the remaining worker wiring: [docs/HARSHITH_HANDOVER.md](docs/HARSHITH_HANDOVER.md).

**2026-10-22 Ruthvik review follow-ups (local):** Branch `feat/ruthvik-review-followups`.
Code measured in this note is `8a0c6bebf9d83fef86765361f165f1b36eb73a7c` (cherry-pick of
`8a4d97b` onto `d519e48`, then accounting and a loopback HTTP client). The three assignment
files stayed `RYAN_WORK.md`, `HARSHITH_WORK.md`, and `RUTHVIK_WORK.md`. No hosted SQL, Auth,
S3, Resend, model endpoint, or Person 4 edits. Migrations `001`–`011` were not edited or
reapplied. `--live-synthetic-eval` was not run. Docker is not installed here, so the CI
image job was not run locally.

Python 3.12.3, uv 0.10.9, PostgreSQL 16 `initdb` on `PATH` via
`/usr/lib/postgresql/16/bin`. `pytest packages evals` **154 passed**, mock evals **3/3 + 6/6**,
platform **333 passed**, control UI **64 passed**. No pytest failures or skips. `staging` and
`production` still refuse both `mock` and `gemini`. Token holds are not a dollar cap. A timeout
with no response body can stay uncounted. Gemini cost is fixture-only until a tariff is
approved. Evidence: [docs/RELEASE_EVIDENCE.md](docs/RELEASE_EVIDENCE.md).
**2026-10-22 Harshith §2a local work (branch `claude/project-thread-rzrjfr`):** New migration
`013_llm_orphaned_holds.sql` (not applied anywhere hosted) adds hold state `expired` and
`llm_expire_orphaned_tokens`; `extract-worker` calls it before the model call so a dead attempt's hold stops
blocking the bill's cap. It needs Ruthvik's agreement before merge. The 012 checks now pass under a
Supabase-like non-superuser `CREATEROLE` owner (11/11), Part A records the server version, and Part C stops on
PostgreSQL < 16. The runbook records the scanner/deployment review (clamd stream limit, 25 MiB bucket versus
email attachments, health checks). Adapter wiring is blocked on Ruthvik; the score consumer entry point awaits
agreement. Tests are listed in the PR. No hosted changes.

**2026-10-22 Harshith staging preparation (documentation only, no hosted access):** Added the migration 012
staging rollout procedure and per-process runtime login table to the runbook, with read-only/rollback-only
checks in `docs/sql/migration_012_staging_checks.sql`; recorded exact missing inputs and the Ruthvik
integration contract (function signatures, score payload, adapter wiring gaps, maker/editor approval gap)
in HARSHITH_WORK. Local check on PostgreSQL 16 (superuser owner): 001–011 recorded, `finledger-platform migrate`
applied only 012, proof 11/11 passed with nothing persisted, negative control 3 rows false; throwaway cluster
only. No application suites rerun; PR #41 CI (71/356/55, mock 3/3 + 6/6) still applies. No hosted changes.

**2026-10-22 Harshith remaining-work refresh:** Updated only his existing HARSHITH_WORK assignment
and this status. PR #41 merged backend deliverables are recorded complete; the active list now
contains approved migration-012 rollout, restricted staging login, private storage, real scanner,
email, hosting, recovery and handoff evidence. Ruthvik retains provider/accounting/runtime wiring,
scoring-to-review integration and detailed QA; no duplicate engineering or extra Ryan QA assignment.
PR #41 CI evidence previously inspected: **71 AI / 356 platform / 55 UI**, mock **3/3 + 6/6**.
Documentation only on `docs/harshith-remaining-work`; no fresh application suites or hosted changes.
Exactly three assignment files retained. Check links/whitespace and exact-commit green CI before merge.

**2026-10-22 platform integration (Harshith, branch `feat/platform-integration`, PR #41):** Added migration
`012_ai_durable_state.sql` (not applied anywhere hosted): `rag_chunks` unique `(client_id, ref)` with
`legacy:`/`auto:` refs for empty values and a hard stop on existing duplicates, `llm_usage_rows`,
`llm_token_reservations`, tenant RLS, `llm_reserve/reconcile/release_tokens` (signatures match
`PostgresKillSwitch`, extract/score/map worker roles only, reconcile/release bound to the caller's tenant)
and one non-login automation maker per firm (`users.automation`; no token, Auth subject, firm-admin flag
or client roles). Added `finledger-platform extract-worker` plus a Compose service (mock provider, local
only): statements and keys outside `{client_id}/` become document exceptions before storage/AI; only
Person 1's fixed failure categories reach `documents.status_reason` (anything else retries under a fixed
reason); success settles `extracted`, a maker-attributed workflow event and a `score` job in one
transaction that re-checks the lease. Retired `packages/testing/sql/ai_durable_ephemeral.sql`: AI durable
tests now run on the real numbered migrations in an ephemeral database.
Local run: **71 AI / 356 platform / 55 UI passed**, mock **3/3 + 6/6**, 0 skipped. Native process smoke
of the Compose services (fresh migrate, `isolation-check` 11/11, upload → `extracted` → `score` queued)
passed; ClamAV ran as the `eicar` stub. Not verified: local Docker build (registry blocked; CI `image`
job builds it), real ClamAV, S3, email, staging Auth E2E, restricted staging login, hosting.
Hosted changes: none. Next owner, Ruthvik: `score` job consumer (payload carries `canonical_invoice` and
`maker_id`), wiring `PostgresKillSwitch` + `PostgresUsageWriter` into `default_extract()` (reserve and
reconcile inside short `tenant()` scopes, not around the model call), and agreeing maker-checker:
whether a human who edits extracted fields becomes the recorded maker (and so cannot approve that bill)
or the automation maker stays maker and any human approver may approve. Owner approval still needed for
hosted login/grants, storage/email/scanner proofs and deployment.

**2026-10-22 three-owner cleanup:** Consolidated all active assignments into RYAN_WORK,
HARSHITH_WORK and RUTHVIK_WORK under `docs/assignments/`. Ruthvik's original release scope
and review follow-ups are together; Harshith retains backend/handover tasks; Ryan stays reduced.
Replaced TEAM_WORK_SPLIT with a short index, updated schema-reference routing, and removed seven
superseded assignment/pointer documents from the current tree. All removed content remains
recoverable in Git history. Shared architecture, operational docs, code, tests and Person 4
were not removed/modified. Historical status filenames below must not be treated as current
assignments. Documentation only on `docs/three-owner-cleanup`; no fresh application suites,
hosted changes or secrets. Validate exact file count, links and whitespace before integration.

**2026-10-22 Ruthvik PR #39 review follow-up:** Inspected open head `1eb9544` and CI run
`39767884426`: **91 AI / 333 platform / 64 UI**, mock **3/3 + 6/6**; test/image jobs passed.
No full local suite rerun. Pure local parser probe reproduced ignored `thoughtsTokenCount`
(input=3/candidates=4/thoughts=1000 returned 3/4) and absent usage accepted as 0/0.
Added RUTHVIK_REMAINING with accounting/output-bound/local HTTP follow-ups, runtime wiring,
Harshith dependencies and gated hosted acceptance. Ruthvik's implementation PR is not merged by
this update; hosted activation remains blocked. Carried corrected recipient routing from
`e79d16f` into this docs PR because those earlier branch corrections were not yet on main.
Ruthvik and Harshith remain separate; Ryan keeps his reduced owner checklist. Documentation only
on `docs/ruthvik-remaining-review`: no runtime fixes, hosted calls/SQL, secrets or Person 4 edits.

**2026-10-22 consolidated release assignment:** Synced main to `cf5d2f0` (PR #37). Added
`docs/assignments/RELEASE_INTEGRATION.md` for remaining AI runtime integration, platform integration,
staging operations and controls/QA, including work previously assigned to Ryan. Existing friend's
accepted migration/consumer handover stays coordinated rather than duplicated. Ryan retains account,
policy/budget approvals, final independent acceptance and launch authority. TEAM_WORK_SPLIT links the
updated assignment. Branch `docs/release-integration-assignment`; documentation only, no hosted changes,
secrets, runtime edits or Person 4 edits. No local suite rerun. PR #37 CI evidence inspected this session:
**71 AI / 333 platform / 55 UI passed**, mock **3/3 + 6/6** (run `39399420351`, October 20).
P1 Gemini fake-transport and durable Postgres adapters now exist; older statements below saying no
adapters exist are historical. Live transport/activation, runtime wiring and production migrations remain
unfinished. New brief requires local link/whitespace validation and green exact-commit PR CI before merge.

**2026-10-17 individual assignment files:** Added three shareable Markdown briefs under
`docs/assignments/`, linked from TEAM_WORK_SPLIT. Friend 1 owns AI, Friend 2 platform;
Ryan is assigned controls/QA as the easiest starting role, without relaxing security review
or owner-only policy/account responsibilities. Branch `docs/individual-team-assignments`.
Documentation only; no local application suite rerun or new test counts claimed. Historical
baseline remains **43 AI / 333 platform / 55 UI**, mock **3/3 + 5/5**. No hosted changes,
credentials, model/email calls, runtime edits or Person 4 edits. Validate links/whitespace
and require exact-commit green CI before integration. PR #35 is merged; no WIP implementation.

**2026-10-17 three-person handoff:** Synced clean main to `346d98a`, including independently
contributed Person 4 files; no edits or readiness assessment of that pack. Added
`docs/TEAM_WORK_SPLIT.md` with three scoped assignments, shared-file ownership, owner gates,
acceptance checks and merge order. Linked it from README and this file; corrected root README's
outdated claim that Person 4 contains architecture only. Cleanup is documentation/navigation only:
no files, volumes, backup branches or private configuration deleted. Branch `docs/three-person-handoff`.
No runtime edits, local suite rerun, hosted SQL/configuration, email or model calls. Historical baseline
remains **43 AI / 333 platform / 55 UI**, mock **3/3 + 5/5**; exact PR CI results must be checked before
integration. PR #33's provider proposal is already merged on main. No unfinished implementation added.
Next: friends select A/B/C roles and complete the first local deliverables while owner gates stay closed.

**2026-10-10 owner decisions and provider research:** Owner delegated maker and statement choices and
asked to research AI providers. Selected per-firm non-login automation maker and statement exclusion for
v1; neither is implemented yet. Added proposed ADR-001 comparing official Google Cloud, Bedrock and
OpenAI documentation, with jurisdiction/retention/lifecycle/capacity caveats and an illustrative cost model.
Conditional recommendation: Gemini 3.5 Flash, India processing only if its provisioned-capacity quote is
approved; EU PayGo is a separate cross-border option requiring explicit approval. No provider/data-transfer
or spending approval is inferred beyond the bounded input choice: owner subsequently permitted minimized
text and bounded page images under proposed controls, selected serving both markets with processing
locations decided later, and deferred budget selection. Location, provider activation, account retention and
exception/spend gates remain unresolved. Do not re-ask maker/statement/input-modality questions.
Branch `docs/ai-provider-options` (PR #33); documentation only, no local test rerun
or new test counts claimed. Runtime baseline remains **333 platform / 55 UI / 43 AI**, mock **3/3 + 5/5**.
No credentials, hosted configuration/SQL, migrations, identities, customer data, email, model calls or Person 4
changes. Next: finalize jurisdiction/routing, verify effective retention and resolve exceptions/budget; then
verify account/capacity before adapter/accounting/consumer implementation. Do not repeat maker/statement
questions. Provider proposal and metadata were checked against official sources; no benchmark or legal
compliance guarantee is claimed. All edits must be committed/pushed through green PR CI before integration.

**2026-10-10 readiness recheck:** Refreshed clean `main` at `43fbe2f` (PR #31 status update merged).
Read this handover completely. Presence-only checks still report S3 access/secret keys, Supabase
publishable/secret keys and Resend key as missing/placeholders; no secret values were displayed.
Owner checklist 5's maker, AI provider/region/data-retention policy and statement choices remain unanswered;
hosting approval, Auth setup and app-login password remain owner gates. No newly unblocked Open work item.
No application changes, fresh test counts, hosted calls/SQL, migrations, identities, email or Person 4 edits.
The verified **333 platform / 55 UI / 43 AI** counts below are from the previous completed batch, not rerun
today. Resume the first owner-unblocked item; do not repeat completed hardening or consume credits simply
to reach the 5% threshold. Today's status branch is `handover/owner-gates-20261010`.

**2026-10-09 — local production-boundary hardening; no deployment performed.**

- Main includes PRs #24–#30 with green `test` and `image` CI. Current published application tip is
  `731f9e9`; final status-only branch is `handover/resource-safety-status`. Integration used non-forced
  fast-forwards of reviewed commits with the owner's noreply identity,
  without attribution trailers. No additional history rewrite was performed. The historical metadata
  correction and local backup branches are documented above; do not repeat or push those backups.
- PR #24 adds [DEPLOYMENT_RUNBOOK.md](docs/DEPLOYMENT_RUNBOOK.md), derived from actual CLI/runtime code.
  README guards existing `.env` files against overwrite. Locked offline project reinstalls repaired stale
  local console launchers; both `.exe --help` checks passed. No dependencies were upgraded.
- PR #25 rejects unknown scanners in every environment and EICAR-only scanning in staging/production.
  PR #26 disables Uvicorn access logs at both CLI launch boundaries to avoid credential-bearing URL logs.
  Operator-managed proxy/custom-server logging still requires private configuration.
- PR #27 rejects malformed signed credentials/claims before object reads, validates finite expiry and safe
  filenames, and encodes Unicode download filenames safely. S3 smoke uses fixed safe failure labels,
  preserves cleanup results after errors, and counts only 401/403/404 as unsigned denial. Other errors,
  including legacy 400, remain unverified/fail-closed. No real S3 proof has been run. Both changes received
  independent review without remaining actionable findings.
- PR #28 requires complete bounded ClamAV replies and exact clean-result framing. Eight regression cases
  failed before implementation; independent offline review found no actionable issue. This does not add
  a whole-operation deadline; the follow-up branch below addresses the socket-I/O budget.
- Fresh complete suites through PR #28: Person 2 **274 passed**, Person 3 **55 passed**, Person 1 **43 passed**;
  mock evaluations **3/3 structural goldens + 5/5 exact matches**. Existing Starlette deprecation warning
  remains. CI supplies Docker image verification; Docker is unavailable locally.
- PR #29 email request-boundary implementation: incremental bounded
  buffering stops at the first oversized chunk; malformed/non-ASCII HMAC headers return 401 before comparison.
  Synthetic ASGI tests: **18 failed / 5 passed** before the fix, **23 passed** afterward. Parent full-platform
  verification passed **297 platform tests**; UI **55 passed**, AI **43 passed**, mock evals **3/3 + 5/5**.
  Independent review found no remaining actionable issue; all PR test/image checks passed before integration.
  Verification commands used the suites above with workspace `--basetemp=.pytest-inbound-platform`,
  `.pytest-inbound-control`, `.pytest-inbound-ai`, and mock report `.pytest-inbound-ai/eval_report.md`.
  Accepted bodies remain buffered for HMAC/MIME
  parsing, with a brief bounded byte-copy; proxy/server limits still matter.
- **Supabase unchanged:** no SQL, migrations, Auth users/invites, hosted settings, provider calls or emails.
  No secret values were read/printed; no Person 4 edits. Presence-only private configuration check still
  found S3, Supabase and Resend keys missing/placeholders. Owner maker/provider-PII/statements questions
  remain unanswered, as do deployment inputs in the Owner checklist. Never guess those decisions.
- PR #30, `hardening/runtime-resource-boundaries`: aggregate scanner socket-I/O deadline has
  **17 failed / 1 passed** regression cases before implementation and **18 passed** afterward; first full
  platform verification **315 passed**. System DNS may overrun connection timeout, but late completion is
  rejected. A synthetic ASGI review reproduced unauthenticated email uploads holding DB connections
  before HMAC validation; the post-auth/threadpool fix has **14 expected red failures**, then **37 focused
  tests passed** with real ASGI dependency ordering and worker threads. Independent review of both fixes
  found no remaining actionable issue. Scanner protocol tests now require a complete INSTREAM request,
  actual reply transmission and propagated server-thread failures. Three harness regression tests failed
  before the fix; **30 scanner/deadline focused tests passed** afterward on real local loopback, without
  thread warnings. This prevents a network-denied run from falsely passing on connection failure.
  Final complete suites: **333 platform / 55 UI / 43 AI passed**, mock evaluations **3/3 + 5/5**,
  with only the existing Starlette warning. Commands used the suites above with workspace basetemps
  `.pytest-resource-final-platform`, `.pytest-resource-final-control`, `.pytest-resource-final-ai`, and
  mock report `.pytest-resource-final-ai/eval_report.md`. All implementation is finished and reviewed;
  both push/PR CI `test` and `image` checks passed on exact commit `731f9e9` before main integration.
  No unfinished implementation or WIP branch. Do not repeat these completed fixes.
- Keep the owner's **5% remaining** hand-back rule. Current usage was above that threshold (28% primary /
  53% weekly remaining at the last check); do not repeat completed tests simply to spend credits.
  Resume the first owner-unblocked item, using the Owner checklist and Next agent task above. Do not guess
  decisions or provision paid resources. PR #31 published the status-only update with green CI and is on
  main; no unfinished implementation remains. No hosted proof or production readiness is claimed.
