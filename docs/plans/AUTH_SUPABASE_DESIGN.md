# Customer sign-in with Supabase Auth: design

Status: **partially implemented.** Migrations 008–011 are merged and applied to staging, with both migration trackers verified. JWT verification, server-owned sessions, and Person 3 login/MFA routes are merged with green CI. The staff invite and first-login (set password) flow is implemented and tested offline with a fake Auth transport; it has never called the live Supabase invite endpoint. Staging Auth configuration, hosting and real-user E2E testing remain open. Scope: Person 2 API + Person 3 control UI. Person 4 untouched.

## 1. Where we are today

| Caller | How it authenticates now | Problem |
|---|---|---|
| Staff in a browser (Person 3 UI) | Server-side Supabase password login, private sessions and TOTP MFA are implemented | Staging login is not live until Auth settings/secrets, a reachable runtime and a real-user E2E test are configured. |
| Staff API / integrations | Bearer API token, sha256-hashed in `users.api_token_hash`, resolved by `auth_user()` | Fine for machines; not for people. |
| Person 4 site agent | Bearer agent token (`/agent/...`) | Fine, stays as is. |
| Vendors | `/i/{public_token}` upload link + inbound email | Fine, no accounts by design. |

Migration `008_supabase_auth_subject.sql` (in Git and on staging) adds `users.auth_subject uuid unique` and the private lookup `finledger_private.auth_user_by_subject(uuid)`: SECURITY DEFINER, `search_path = pg_catalog`, EXECUTE only for `finledger_app`.

## 2. Decision

Use **Supabase Auth for people only**, behind our own servers:

- Supabase Auth owns identities: email + password or email OTP, password reset, **TOTP MFA**, and SAML SSO later (Pro plan feature).
- **Our Python servers own sessions and authorization.** The browser never talks to Postgres or the Supabase Data API. Data API grants stay revoked (006), and tenancy stays enforced by our `set_config('app.client_id'/'app.firm_id')` + RLS as `finledger_app`.
- Supabase JWTs are verified by our servers and mapped to a `users` row via `users.auth_subject = jwt.sub`. We do **not** switch RLS to `auth.uid()`; that would couple tenancy to the Data API we deliberately closed.
- Bearer API tokens remain for machines (integrations, Person 4 agent).

Why not keep the reverse-proxy token injection? It pushes login, MFA and session security into infrastructure we don't have. Why not raw `auth.uid()` RLS? It reopens the Data API and splits the authorization model in two.

## 3. Flows

### 3.1 Sign-in (server-side, no SPA needed)
1. `GET /login` (Person 3) renders email + password form (or "email me a code").
2. Server calls Supabase Auth: `POST {SUPABASE_URL}/auth/v1/token?grant_type=password` (or `/otp` + `/verify`) with the **publishable** key.
3. On success, server verifies the returned access token (section 4), then stores `{access, refresh, expires_at, user_id, aal}` in a **server-side session** row and sets cookie `__Host-fl_session=<random 256-bit id>`; `HttpOnly; Secure; SameSite=Lax; Path=/`.
4. Tokens never reach browser JavaScript, never appear in URLs or logs.

### 3.2 Every request
1. Look up session by cookie → refresh with `grant_type=refresh_token` if the access token expires in < 60 s (Supabase rotates refresh tokens; store the new one).
2. Verify JWT → `sub` → `finledger_private.auth_user_by_subject(sub)` (from 008).
3. Existing authorization continues unchanged: `user_client_roles()`, SoD rules, CSRF double-submit cookie (already in Person 3).

### 3.3 MFA
- Browser staff must have `aal2` (verified TOTP) in the JWT `aal` claim before seeing client data; otherwise redirect to `/mfa`. This is stricter than the initial firm-admin/approver/payer minimum and matches the owner's password-plus-TOTP decision.
- Enrolment/challenge via `/auth/v1/factors` endpoints, server-side, using the user's own access token.
- Successful verification atomically replaces the AAL1 cookie hash with a new random cookie hash (migration 010), preserving the original absolute expiry.

### 3.4 Invites and first login (no public sign-up)
- Turn **off** public sign-ups in Supabase Auth settings.
- Implemented in `finledger_platform/staff_invites.py` (service + `SupabaseAdminClient`) and exposed as Person 3
  browser routes `GET /admin/staff` and `POST /admin/staff/invite`. Browser routes are used because only the
  cookie session carries the verified `aal`; bearer API tokens have no assurance level and cannot invite.
- Gate: firm admin **and** AAL2 from the resolved browser session, re-checked against `users.firm_admin` inside
  the firm-scoped transaction; login CSRF + same-origin check. Roles are limited to `ap_clerk`, `approver`,
  `payer`; every client must belong to the inviter's firm (RLS plus an explicit firm check). Firm admin is an
  explicit checkbox.
- Order (Auth and Postgres cannot share a transaction, so it fails closed):
  1. Commit a pending `users` row with `auth_subject` NULL and no API token, plus its client memberships. An
     unlinked row can never sign in: there is no email fallback.
  2. `POST /auth/v1/invite` with the **secret key**. Failure leaves only the unlinked row; re-submitting the same
     email retries and **replaces** (never accumulates) the pending grants.
  3. Link the returned Supabase user id with compare-and-set (`auth_subject is null`). If that fails, delete the
     new Auth user only when no row is linked to it; if the delete also fails, log the Auth user id (no email)
     for manual removal. The orphan identity cannot reach data because it is unlinked.
- Duplicates: an email that is already linked can only be **re-sent** (email alone; roles never change through
  this form) and the returned id must equal the stored link. An email used in another firm, or a pre-existing
  API-token account, is a generic conflict and is never linked by email. Such legacy accounts are linked by an
  operator, as before.
- First login: the invite email links to `GET /auth/accept?token_hash=…&type=invite`, which only renders a form
  (mail scanners prefetch links). `POST /auth/accept` checks the password (12–1024 characters, before the
  one-time token is spent), redeems it with `POST /auth/v1/verify`, requires the subject to be **already linked**,
  sets the password with `PUT /auth/v1/user`, signs that temporary Auth session out and redirects to `/login`.
  The person then signs in and must enrol TOTP (AAL2) before seeing client data.
- Owner setup before staging E2E: set `SUPABASE_SECRET_KEY` only in the control UI's server secret settings;
  in Supabase → Auth → Email templates → *Invite user*, point the link at
  `{{ .SiteURL }}/auth/accept?token_hash={{ .TokenHash }}&type=invite`; set Site URL / redirect allow-list to the
  control UI origin; confirm in staging that re-inviting an unaccepted user returns the same user id.

### 3.5 Sign-out and revocation
- `POST /logout`: delete server session, call `/auth/v1/logout` (revokes refresh token).
- Admin removes a user: delete membership rows + session rows; their JWTs die at expiry (keep access-token lifetime ≤ 15 min in Supabase settings).

## 4. JWT verification
- Use Supabase **asymmetric signing keys**; verify with JWKS from `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` (cache, refresh on unknown `kid`). No shared `SUPABASE_JWT_SECRET` needed.
- Check `iss == {SUPABASE_URL}/auth/v1`, `aud == "authenticated"`, `exp`, `role == "authenticated"`, and `aal` where required.
- Library: `PyJWT[crypto]` (new dependency).

## 5. Data model changes (one new migration, `009_...`)
- `finledger_private.staff_sessions` (migration 009, merged): SHA-256 cookie hash, user_id, Fernet-encrypted access and refresh tokens, access-token expiry, 30-minute idle and 8-hour absolute limits, compare-and-swap version. RLS on, no app/API table grants; narrow private SECURITY DEFINER functions only.
- `finledger_private.staff_session_events` records session start, revocation and successful MFA verification. Failed-login audit events are not implemented; do not treat these session events as a complete authentication audit trail.
- Migration 010 adds `mfa_verified` audit events and the atomic cookie-rekey function. Migration 011 aligns the two migration trackers. Both are applied to staging.

## 6. Configuration

| Variable | Secret? | Used by |
|---|---|---|
| `SUPABASE_URL` | no | API + control UI |
| `SUPABASE_PUBLISHABLE_KEY` (anon) | no, but keep server-side | login/refresh/OTP/MFA calls |
| `SUPABASE_SECRET_KEY` (service role) | **yes**, secret manager | control UI invite path only (`SupabaseAdminClient`); unset = invites disabled |
| `FINLEDGER_ENC_KEY` | yes | encrypting stored refresh tokens |

Supabase dashboard settings: disable sign-ups, Site URL + redirect allow-list = our domain, access-token expiry 900 s, enable TOTP MFA, custom SMTP (Resend, verified sender domain) for auth emails, leaked-password protection on.

## 7. Build order
1. ~~Migration 008~~ done.
2. ~~`009` sessions + lookup function + tests~~ merged in PR #7 and applied to staging, followed by migrations 010 and 011.
3. ~~JWT verifier module + unit tests~~ merged. Server-side Auth client and session manager merged in PR #8.
4. ~~Person 3 `/login`, `/logout`, `/mfa` and cookie-backed browser routes~~ merged in PR #9 with green CI. Legacy bearer remains for local development; staging/production fail startup without browser Auth config. All browser staff need AAL2 before client data.
5. ~~Invite endpoint behind firm-admin + aal2, plus first-login password set~~ implemented and tested offline (fake Auth). Needs the owner setup in §3.4 before any live use.
6. Staging: configure the Supabase settings in section 6, create one test firm, run an end-to-end browser login (Playwright) including MFA.

## 8. Owner decisions (answered)
- Password plus TOTP MFA; no email-code-only login.
- Defer SSO until a customer requires it and the plan supports it.
- Eight-hour absolute and 30-minute idle limit for approvers/payers. Migration 009 currently applies these stricter limits to **all** browser staff sessions.
