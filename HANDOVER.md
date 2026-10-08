# FinLedger handover (shared by ChatGPT/Codex and Claude)

This file is the single baton between AI agents working on FinLedger. **Read all of it before touching code.**
Whoever is working updates the "Latest status" section and pushes it before stopping. It replaces the old
`HANDOVER_CLAUDE.md`.

---

## 🔁 Hand-back protocol (MANDATORY, read first)

When your credits/usage are running low (**start this at ~15% remaining, not at 0%**):

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

## Repository

- GitHub: `ryanshaon/FinLedger`. Default branch `main` (protected by habit: changes go via PR, CI must be green).
- Windows checkout: `C:\Users\ryans\OneDrive\Documents\Studies\Projects\FinTechShi\FinLedger_GitHub`.
- Layout: `packages/`, `evals/`, `person1_ai_rag/` (Person 1 AI/RAG) · `person2_platform/` (API, tenancy, DB,
  workers, storage, auth) · `person3_control_ui/` (controls, workflow, review UI) · `person4_tally/` (out of scope).

## How to verify (same commands as CI, `.github/workflows/ci.yml`)

```bash
uv sync --project person2_platform --python 3.12 --locked --all-extras --dev
uv sync --project person3_control_ui --python 3.12 --locked --dev
PYTHONPATH=packages:evals person2_platform/.venv/bin/python -m pytest packages evals -q
PYTHONPATH=packages:evals person2_platform/.venv/bin/python evals/run_evals.py --provider-mode mock
PYTHONPATH=person2_platform/src person2_platform/.venv/bin/python -m pytest person2_platform/tests -q
PYTHONPATH=person2_platform/src:person3_control_ui/src person3_control_ui/.venv/bin/python -m pytest person3_control_ui/tests -q
docker build -t finledger:ci .        # CI "image" job
```

As of merged PR #8: **Person 1 41 passed, evals 3/3 + 5/5, Person 2 127 passed, Person 3 38 passed**;
PR #7 and #8 had green `test` + `image` CI. Re-run exact counts after subsequent merges.

- DB tests start a throwaway PostgreSQL via `initdb` (needs PostgreSQL on PATH). As root they run it as the
  `postgres` OS user automatically. On Windows the sandbox used to stall `initdb`: prefer CI or WSL.
- `run_evals.py` rewrites the timestamp in `evals/latest_report.md`; `git checkout -- evals/latest_report.md` unless
  you intend to commit a new report.
- If you change `person2_platform/pyproject.toml` dependencies, run `uv lock` in **both** `person2_platform` and
  `person3_control_ui` (Person 3 depends on Person 2), then `uv lock --check` both.

## Supabase staging (project `hxymklwqifwojziewtcv`, region `ap-south-1`, no customer data)

State verified 2026-10-08:
- `public.schema_migrations` has `001`–`008`, matching the repo files exactly. **Do not re-apply them.**
  Migration 009 is merged but not yet applied; migration 010 is on `handover/auth-ui` until its PR is green.
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
| Supabase JWT verifier + Auth/session backend | `finledger_platform/supabase_auth.py`, `auth_gateway.py`, `browser_sessions.py` | tested offline and with local Postgres; PR #7/#8 merged. Browser routes are on `handover/auth-ui`, not live on staging |
| Staging proof tools | `finledger-platform isolation-check`, `finledger-platform s3-smoke` | tested; s3-smoke never run against real Supabase (no valid keys yet) |
| Runtime image + local stack | `Dockerfile`, `compose.yaml` | built and run: API + control UI healthy, worker up |
| Control UI entry point | `finledger-control` | uses `make_pool` (RLS guard) and full S3 settings |
| Auth design | `docs/plans/AUTH_SUPABASE_DESIGN.md` | partially implemented; invite and staging E2E remain |

## Open work, in priority order

1. **S3 smoke test against Supabase.** Blocked on the user creating real S3 keys (Supabase → Storage → S3 →
   New access key) and putting them in environment settings, never in chat. Then
   `FINLEDGER_STORE=s3 ... finledger-platform s3-smoke`.
2. **Sign-in (Supabase Auth)**, per `docs/plans/AUTH_SUPABASE_DESIGN.md` section 7:
   - migration `009` private browser sessions merged in PR #7, **not applied to staging**. Migration `010` MFA
     cookie rekey is on `handover/auth-ui`, pending PR/CI. Apply both in order after they are committed/merged;
     never run SQL without its matching committed migration.
   - Person 3 `/login`, `/logout`, `/mfa`, and cookie-backed browser routes are on `handover/auth-ui`; keep bearer
     tokens for API/agent routes. Local P2 130 passed and P3 43 passed with this branch, pending CI.
   - invite endpoint (firm admin + aal2), which sets `auth_subject` explicitly
   - **Owner decisions answered:** password + TOTP MFA; defer SSO; 8 h absolute and 30 min idle browser sessions.
     The implementation gates all browser staff on AAL2 and applies the stricter timeouts to all staff.
3. **App login role on staging** (`fl_app` or similar, member of `finledger_app` + worker roles), password chosen
   by the user and stored in secret settings; connect via the Supabase **session pooler**.
4. **Email**: Resend outbox delivery is implemented; needs a verified sender domain and key. Inbound domain/webhook
   integration untested.
5. **Hosting**: not provisioned. Recommendation: AWS ECS/Fargate in Mumbai, colocated with Supabase `ap-south-1`
   (Render is the simple alternative). Image is ready. Needs account + approval before paid resources.
6. **Person 1**: audit live provider path, persistence, PII handling, kill switch. Mock evals passing is not
   production AI readiness.
7. **Performance**: Supabase advisor lists 27 unindexed foreign keys; index with real workload in mind. Don't drop
   "unused" indexes on an empty DB.

---

## Latest status

**2026-10-08, Claude → ChatGPT.** `main` @ `1eb2bbd`, CI green, working tree clean on Windows and in cloud.
Merged today: PR #1 (root-safe test harness, isolation-check, s3-smoke), PR #2 (Docker/compose, Person 3 RLS + S3
fixes, auth design), PR #3 (migration 008 recovered verbatim from Supabase history), PR #5 (ChatGPT's unpushed JWT
verifier from Windows + key_ops fix + Person 3 relock). The user is currently creating real Supabase S3 keys.
Nothing half-done, no WIP branches.

<!-- Next agent: replace the paragraph above with your own status when you hand back. Keep it short and exact. -->
