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

As of merged PR #10: Person 2 **131 passed** and Person 3 **43 passed** locally; PRs #7–#10 had green
`test` + `image` CI. Person 1 baseline was 41 passed and evals 3/3 + 5/5 before the readiness guard;
re-run exact counts after subsequent merges.

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
| Runtime image + local stack | `Dockerfile`, `compose.yaml` | built and run: API + control UI healthy, worker up |
| Control UI entry point | `finledger-control` | uses `make_pool` (RLS guard) and full S3 settings |
| Auth design | `docs/plans/AUTH_SUPABASE_DESIGN.md` | partially implemented; invite and staging E2E remain |

## Open work, in priority order

1. **S3 smoke test against Supabase.** Blocked on the user creating real S3 keys (Supabase → Storage → S3 →
   New access key) and putting them in environment settings, never in chat. Then
   `FINLEDGER_STORE=s3 ... finledger-platform s3-smoke`.
2. **Sign-in (Supabase Auth)**, per `docs/plans/AUTH_SUPABASE_DESIGN.md` section 7:
   - Migrations `009` private sessions, `010` MFA cookie rekey and `011` migration-history alignment are merged,
     green in CI and applied to staging. Never re-apply them.
   - Person 3 `/login`, `/logout`, `/mfa`, and cookie-backed browser routes are merged in PR #9; keep bearer
     tokens for API/agent routes. Local P2 131 passed and P3 43 passed; staging Auth secrets/settings, real-user
     E2E, and hosting remain open.
   - invite endpoint (firm admin + aal2), which sets `auth_subject` explicitly
   - **Owner decisions answered:** password + TOTP MFA; defer SSO; 8 h absolute and 30 min idle browser sessions.
     The implementation gates all browser staff on AAL2 and applies the stricter timeouts to all staff.
3. **App login role on staging** (`fl_app` or similar, member of `finledger_app` + worker roles), password chosen
   by the user and stored in secret settings; connect via the Supabase **session pooler**.
4. **Email**: Resend outbox delivery is implemented; needs a verified sender domain and key. Inbound domain/webhook
   integration untested.
5. **Hosting**: not provisioned. Recommendation: AWS ECS/Fargate in Mumbai, colocated with Supabase `ap-south-1`
   (Render is the simple alternative). Image is ready. Needs account + approval before paid resources.
6. **Person 1**: audit recorded in `docs/audits/PERSON1_PRODUCTION_READINESS.md` (PR #11 merged). Live provider,
   persistent RAG/usage/cap state, extract queue consumer and PII policy remain release blockers. Mock evals are
   not production AI readiness.
7. **Performance**: Supabase advisor now lists 28 unindexed foreign keys (the extra one is the new private
   `staff_session_events.user_id` FK) and 13 “unused” indexes on this empty database. Prioritize indexes from
   real query plans and FK delete/update costs; do not drop useful indexes based on zero-data statistics.

---

## Overnight autonomous run (2–3 hours; owner unavailable)

This is the work order for Claude's next unattended session. **The Open work list above remains the product
priority; this section selects the first items that can safely move without waking the owner.** Read this whole
file and the linked design/audit before editing. Start from a clean, freshly pulled `main`; never use this
handover branch as a code-development base after it is merged. If a prior branch or PR is already in flight,
inspect it first and avoid duplicate work.

### 0. Operating envelope (first 10 minutes)

- `git switch main`, `git pull`, `git status --short --branch`; inspect current PRs and CI before branching.
- Scope is Persons 1–3 only; never edit `person4_tally/`. Never print or commit `.env`, keys, tokens, passwords,
  invoice content, or customer data. Do not create paid resources, real users, or send real invites/emails.
- Every change gets a focused branch, PR, and green CI before merge. Do not push directly to `main`. If GitHub,
  CI, or credentials are unavailable, push a clearly labeled WIP branch if possible and record the limitation;
  never claim a change is merged or verified when it is not.
- Never apply SQL to Supabase without committing the identical migration in the same session. For this run,
  prefer **offline implementation/tests only**: do not change staging Auth settings, roles, data, or schemas
  unless a reviewed migration and a non-destructive test plan make the change unambiguously safe.
- Use the owner's settled decisions: password + TOTP MFA; SSO deferred; 8-hour maximum and 30-minute idle
  browser sessions. Do not re-open those questions.

### 1. Primary deliverable: safe staff invitation flow (roughly 90–120 minutes)

Implement the remaining Person 2 invitation flow from `docs/plans/AUTH_SUPABASE_DESIGN.md` §3.4/§7 as a
separate PR. Read `person2_platform/src/finledger_platform/{auth_gateway,supabase_auth,browser_sessions}.py`,
the existing API/permission patterns and their tests first. Keep the secret/service-role key **server-side only**.
The API must require a real firm-admin identity with AAL2, authorize within the correct firm, validate roles,
explicitly bind the returned Auth subject to the intended `users` row, and never auto-link on email/JWT metadata.
Handle duplicate invites, Auth failures, and DB failures without silently leaving a usable but unlinked account;
document any compensating action that cannot be atomic across Supabase Auth and Postgres. Preserve bearer-token
machine/agent paths and existing RLS boundaries. Use a fake Auth transport and local/ephemeral Postgres for tests;
**do not call the live Supabase invite endpoint** or create a real identity while the owner sleeps.

Acceptance: tests prove unauthorized and AAL1 callers are denied, cross-firm and role escalation are denied,
the success path binds exactly one Auth subject, retries/duplicates are safe, and failures cannot grant access.
Run Person 2 and Person 3 suites plus relevant Person 1 contracts. If the flow needs an unanswered policy choice
or a real secret to finish, implement only the independently testable part, mark the PR/WIP accurately, and move
to stage 2 without waiting for the owner.

### 2. Secondary deliverable: Person 1 queue boundary (remaining time)

After the invitation PR is green/merged—or if it becomes genuinely blocked—take the first safe slice of the
P0 extract-consumer blocker in `docs/audits/PERSON1_PRODUCTION_READINESS.md`. Trace the existing `extract` job
producer, queue claim/ack/retry/dead-letter semantics, Person 1 extraction contract, and tenant context before
editing. Prefer a narrow, tested worker integration or contract tests that fail clearly until the real pipeline
is wired. Do not turn on mock AI in staging/production, send invoices to a model provider, claim full extract →
score → map E2E, or invent a PII/provider policy. Keep each independently verified slice in its own PR.

If there is not enough time for safe code, deliver an exact implementation plan in the audit: entry points,
data flow, required tenant isolation, retry/dead-letter behavior, test cases and unresolved decisions. Clearly
label documentation-only output as such. Do not spend the night repeatedly polling blocked S3 keys, email domain,
hosting, provider/PII approval, or app-role credentials. Do not blanket-create/drop indexes from the empty
staging database's advisor notices.

### 3. Verification and morning hand-back (reserve final 20–30 minutes)

- Run the relevant commands under **How to verify**, record **actual** pass counts, and check GitHub `test` and
  `image` jobs for every PR. A previous pass count is a baseline, not proof for a new change.
- Review the diff for secrets, generated files, accidental Person 4 edits, unsafe SQL, and scope creep. Report
  whether staging changed; if it did, list exact migration files and evidence. Never say “production ready.”
- Update **Latest status** with PR links/branches, commits, actual test counts, what remains half-done and what
  the owner must do. Commit and push this update via a PR too. Finish with a clean checkout on `main`; if work
  remains unfinished, commit it on `wip/<topic>` with a `WIP:` message, push it and name it in the status.
- If usage approaches 15% before the timebox ends, invoke the mandatory hand-back protocol above immediately.
  Do not leave uncommitted files on the PC. A short, truthful handover beats an unverified last-minute feature.

---

## Latest status

**2026-10-09, ChatGPT → Claude (overnight brief).** `main` @ `11b11e6` before the overnight-brief PR. Windows
checkout was clean before this handover edit. PRs #7–#12 merged with green `test` and `image` CI:

- #7: migration 009 private browser sessions; #8: Supabase Auth gateway and encrypted session backend; #9:
  Person 3 login, logout, TOTP MFA, AAL2 gate and migration 010 atomic cookie rekey; #10: migration 011 aligning
  Supabase and Python migration histories; #11: Person 1 production-readiness audit and hosted-mock fail-closed
  guard; #12: shared handover/status update.
- Fresh local verification: Person 1 `packages evals` **43 passed**; Person 2 **131 passed**; Person 3 **43 passed**.
  The mock eval runner's 3/3 + 5/5 baseline was not rerun this session. Each PR's GitHub CI jobs were green.
- Supabase staging `hxymklwqifwojziewtcv`: committed migrations **009, 010, 011 applied in order** after green PRs;
  both migration histories now contain 001–011. `staff_sessions` and `staff_session_events` have RLS on, no direct
  SELECT grants to API/app roles, and zero rows. Security advisor has only three intentional INFO no-policy items.
  No identities, credentials or customer data were added. Performance advisor: 28 unindexed FKs; 13 unused-index
  notices on an empty DB. No other Supabase changes.
- Incomplete: real S3 smoke needs user-provided keys; Auth needs publishable/secret keys in server secret settings,
  Supabase project Auth settings, verified sender domain, app role and hosting before staging E2E. Invite flow is
  not implemented. Person 1 live provider, queue consumer, durable state and PII policy are release blockers;
  see `docs/audits/PERSON1_PRODUCTION_READINESS.md`. No Person 4 files were edited.
- No half-done code or WIP branch. Continue Open work in priority order. Treat all mock keys as placeholders and
  never claim production readiness until these blockers are closed. For the owner's 2–3 hour unattended window,
  execute the bounded **Overnight autonomous run** above and update this paragraph before handing back.

<!-- Next agent: replace the paragraph above with your own status when you hand back. Keep it short and exact. -->
