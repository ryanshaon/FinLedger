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

## Repository

- GitHub: `ryanshaon/FinLedger`. Default branch `main` (protected by habit: changes go via PR, CI must be green).
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
```

Baseline as of merged PR #15: Person 1 **43 passed**, evals 3/3 + 5/5, Person 2 **166 passed**, Person 3
**50 passed**; `test` + `image` CI green. Re-run exact counts after later merges.
- Cloud containers: PostgreSQL may be installed but not on PATH. Use
  `export PATH=/usr/lib/postgresql/16/bin:$PATH`.

- DB tests start a throwaway PostgreSQL via `initdb` (needs PostgreSQL on PATH). As root they run it as the
  `postgres` OS user automatically. On Windows the sandbox used to stall `initdb`: prefer CI or WSL.
- `run_evals.py` rewrites the timestamp in `evals/latest_report.md`; avoid running it just for a handover. If you
  run it, either include the updated report intentionally or restore only that generated file after checking the
  diff. Do not discard unrelated work.
- If you change `person2_platform/pyproject.toml` dependencies, run `uv lock` in **both** `person2_platform` and
  `person3_control_ui` (Person 3 depends on Person 2), then `uv lock --check` both.

## Supabase staging (project `hxymklwqifwojziewtcv`, region `ap-south-1`, no customer data)

State verified 2026-10-09:
- Both Supabase's migration history and `public.schema_migrations` include `001`–`011`. Migrations 009, 010 and
  011 were applied from the exact committed files after green PRs #7, #9 and #10. **Do not re-apply them.**
- `finledger_private.staff_sessions` and `staff_session_events` have RLS enabled and no direct SELECT grants to
  `anon`, `authenticated` or `finledger_app`; all have zero rows. Security advisor only reports intentional INFO
  “RLS enabled, no policy” for these two tables and `schema_migrations`.
- 24 public tables, all RLS on; `anon`/`authenticated`/`service_role` have no table grants (006).
- Security advisor: only the intentional INFO "RLS enabled, no policy" on `schema_migrations`.
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
| Runtime image + local stack | `Dockerfile`, `compose.yaml` | image includes installed `finledger-ai` (PR #17); image CI verifies imports, resources and hosted mock guards. No extract consumer |
| Control UI entry point | `finledger-control` | uses `make_pool` (RLS guard) and full S3 settings |
| Staff invites + first login | `finledger_platform/staff_invites.py`, Person 3 `/admin/staff`, `/auth/accept` | merged in PR #15; tested offline with fake Auth only. Disabled until `SUPABASE_SECRET_KEY` is set |
| Auth design | `docs/plans/AUTH_SUPABASE_DESIGN.md` | implemented except staging Auth settings, hosting and real-user E2E |

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
   `FINLEDGER_S3_ENDPOINT_URL=https://hxymklwqifwojziewtcv.supabase.co/storage/v1/s3`,
   `FINLEDGER_S3_REGION=ap-south-1`, `FINLEDGER_S3_ADDRESSING_STYLE=path`, and **type** `AWS_ACCESS_KEY_ID` /
   `AWS_SECRET_ACCESS_KEY` yourself. `.env` is gitignored; never paste the keys into chat. Then run
   `finledger-platform s3-smoke` (open item 1).
4. **Supabase invite setup** (before any real invite), from `AUTH_SUPABASE_DESIGN.md` §3.4:
   - put `SUPABASE_SECRET_KEY` only in the control UI server's secret settings;
   - Supabase → Auth → Email templates → *Invite user*: link =
     `{{ .SiteURL }}/auth/accept?token_hash={{ .TokenHash }}&type=invite`;
   - set Site URL + redirect allow-list to the control UI origin; turn public sign-ups **off**;
   - access-token expiry 900 s; enable TOTP MFA; leaked-password protection on.
5. **Answer three Person 1 questions** (details in `docs/audits/PERSON1_PRODUCTION_READINESS.md`, "Extract consumer"):
   - a. Who is the "maker" for automated scoring? (Recommended: one non-login "FinLedger automation" user per firm.)
   - b. Which AI provider/region is allowed, and may invoice text and page images leave FinLedger? Retention and
     logging rules?
   - c. Statements: skip for v1 (recommended) or extract?
6. **Choose a password for the staging app login role** (open item 3) and store it only in secret settings.
7. **Email:** verify a sender domain in Resend and store the API key in secret settings (open item 4).
8. **Hosting:** approve an account and budget (open item 5).

## Open work, in priority order

| # | Item | State | Blocked on |
|---|---|---|---|
| 1 | **S3 smoke test against Supabase** (`FINLEDGER_STORE=s3 … finledger-platform s3-smoke`) | code ready, never run live | owner checklist 3 |
| 2 | **Sign-in staging E2E** (Playwright: invite → set password → login → TOTP → client list) | all code merged (PRs #7–#9, #15); migrations 009–011 applied, **never re-apply** | owner checklist 4 + hosting |
| 3 | **App login role on staging** (`fl_app`-style, member of `finledger_app` + worker roles, via the Supabase **session pooler**) | not created | owner checklist 6 |
| 4 | **Email delivery** (Resend outbox implemented; inbound webhook untested) | code ready | owner checklist 7 |
| 5 | **Hosting** (recommendation: AWS ECS/Fargate Mumbai next to Supabase `ap-south-1`; Render is the simple alternative) | image ready | owner checklist 8 |
| 6 | **Person 1 release blockers:** live provider, extract queue consumer, persistent RAG/usage/cap state, PII policy | plan in the audit (documentation only) | owner checklist 5 (except 6a below) |
| 6a | **Package Person 1 into the runtime image** | implemented and verified in PR #17 | none; no provider or consumer added |
| 7 | **Performance:** 28 unindexed FKs, 13 "unused" indexes on an empty DB | do not act on zero-data advisor stats | real query plans |

Owner decisions already answered (do not re-ask): password + TOTP MFA; SSO deferred; 8 h absolute / 30 min idle
browser sessions, applied to all staff with AAL2 required before client data.

## ▶️ Next agent task (safe without the owner)

**Person 1 packaging is done (PR #17). Do not repeat it.** The safe remaining code task is to prevent raw
ingestion exception messages/tracebacks from leaking invoice content or credentials into `jobs.last_error`
or application logs, as identified in `docs/audits/PERSON1_PRODUCTION_READINESS.md`.

1. Use a fresh focused branch and PR. Read `worker.py`, `prep.py`, `virus.py` and the queue/ingest tests.
2. Keep operationally useful fixed error categories; never persist or log the untrusted exception message.
   Preserve permanent/dead outcomes, retry/backoff and the scanner fail-closed behavior.
3. Prove with synthetic canary tests that queue diagnostics and captured logs do not contain exception content;
   run the full Person 2 suite and CI. No hosted SQL or secrets are needed.

Then, if the owner has answered checklist 5, follow the consumer design and test list in the audit. Otherwise
leave the consumer blocked and list the exact missing choices. Packaging is not live AI readiness.

---

## Latest status

**2026-10-09, packaging checkpoint.** Started from rewritten `main` @ `4256f6d` (PR #16). Work branch:
`handover/person1-runtime-packaging`, [PR #17](https://github.com/ryanshaon/FinLedger/pull/17).

- Aligned the clean Windows checkout with rewritten history; retained the old tip only on the local archive
  branch named in owner checklist 1. No user edits or `.env` were overwritten. Commits use the owner's identity
  without AI attribution. The owner changed the low-usage hand-back threshold to **5% remaining**.
- Person 1 now ships as the `finledger-ai` wheel: all eight module groups, three prompt resources and six JSON
  Schemas, with a direct Pydantic dependency. Person 2 installs it; Person 3 inherits it. Both locks refreshed
  with no unrelated dependency upgrades. Docker installs it non-editably; CI tests no longer inject `packages`
  into `PYTHONPATH`. Fixed the exporter's installed import and the Windows test runner's environment selection.
- Fresh local checks (Python 3.12 / PostgreSQL 18, app-side non-owner test roles): Person 1 **43 passed**;
  Person 2 **166 passed**; Person 3 **50 passed**. Mock evaluations **3/3 + 5/5**, report written under ignored
  test output. Both `uv lock --check` commands passed. An isolated standalone wheel, outside the checkout,
  passed **9 imports, 3 prompts, 6 schemas and 2 hosted mock guards**. Windows runner syntax checked.
- PR #17 `test` and `image` CI passed on implementation commit `91b39c7`, including `docker build` and isolated
  image checks. Docker is unavailable locally. The handover-only follow-up must also get green CI before merge.
- **Supabase unchanged:** no SQL, settings, identities or invites; no new migrations. No Person 4 edits.
  No half-done implementation. The consumer remains documentation only and blocked on owner checklist 5;
  S3/Auth/email/hosting still need the listed owner inputs. Next safe task: ingestion diagnostic sanitization above.

<!-- Next agent: replace the paragraph above with your own status when you hand back. Keep it short and exact. -->
