# Customer sign-in with Supabase Auth: design

Status: **proposed, not implemented.** Scope: Person 2 API + Person 3 control UI. Person 4 untouched.

## 1. Where we are today

| Caller | How it authenticates now | Problem |
|---|---|---|
| Staff in a browser (Person 3 UI) | `Authorization: Bearer <api token>` that a reverse proxy/SSO layer must inject | No login page, no sessions, no MFA, no password reset. Nothing real injects the token yet. |
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
- Staff with `firm_admin` or `approver`/`payer` roles **must** have `aal2` (verified TOTP) in the JWT `aal` claim; otherwise redirect to `/mfa`.
- Enrolment/challenge via `/auth/v1/factors` endpoints, server-side, using the user's own access token.

### 3.4 Invites and first login (no public sign-up)
- Turn **off** public sign-ups in Supabase Auth settings.
- Firm admin invites `email` + roles → we create the `users` row (no `auth_subject`) and call Supabase **admin** invite (`/auth/v1/invite`), which needs the **secret/service-role key**. Only this one code path, in the API process, ever holds it.
- **No automatic linking by email or JWT metadata** (the rule in 008). The invite response returns the new Supabase user id; the server stores it as `auth_subject` on the pre-created `users` row within the same firm-admin request. A login whose `sub` is unlinked gets "account not provisioned", never a fallback match. The unique constraint prevents one identity from binding to two users.

### 3.5 Sign-out and revocation
- `POST /logout`: delete server session, call `/auth/v1/logout` (revokes refresh token).
- Admin removes a user: delete membership rows + session rows; their JWTs die at expiry (keep access-token lifetime ≤ 15 min in Supabase settings).

## 4. JWT verification
- Use Supabase **asymmetric signing keys**; verify with JWKS from `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` (cache, refresh on unknown `kid`). No shared `SUPABASE_JWT_SECRET` needed.
- Check `iss == {SUPABASE_URL}/auth/v1`, `aud == "authenticated"`, `exp`, `role == "authenticated"`, and `aal` where required.
- Library: `PyJWT[crypto]` (new dependency).

## 5. Data model changes (one new migration, `009_...`)
- `sessions` table (server-only, RLS on, no policies, no API-role grants): id (hash of cookie value), user_id, refresh token **encrypted with `FINLEDGER_ENC_KEY`**, access token expiry, aal, created/last_seen, ip/user-agent.
- Audit rows for login, MFA enrolment, failed login, logout.

## 6. Configuration

| Variable | Secret? | Used by |
|---|---|---|
| `SUPABASE_URL` | no | API + control UI |
| `SUPABASE_PUBLISHABLE_KEY` (anon) | no, but keep server-side | login/refresh/OTP/MFA calls |
| `SUPABASE_SECRET_KEY` (service role) | **yes**, secret manager | invite endpoint only |
| `FINLEDGER_ENC_KEY` | yes | encrypting stored refresh tokens |

Supabase dashboard settings: disable sign-ups, Site URL + redirect allow-list = our domain, access-token expiry 900 s, enable TOTP MFA, custom SMTP (Resend, verified sender domain) for auth emails, leaked-password protection on.

## 7. Build order
1. ~~Migration 008~~ done.
2. `009` sessions + lookup function + tests (local throwaway Postgres as today).
3. JWT verifier module + unit tests with a locally generated JWKS (no network).
4. Person 3 `/login`, `/logout`, `/mfa`, session middleware replacing the `Authorization` header dependency for browser routes; keep bearer for API routes.
5. Invite endpoint (Person 2) behind firm-admin + aal2.
6. Staging: configure the Supabase settings in section 6, create one test firm, run an end-to-end browser login (Playwright) including MFA.

## 8. Open questions for the owner
- Password + OTP, or OTP-only (passwordless)? Recommendation: password + mandatory TOTP for staff; OTP optional fallback.
- Is SSO (SAML) needed for the first customers? It needs the Supabase Pro plan.
- Session lifetime for idle staff (recommend 8 h absolute, 30 min idle for approvers/payers).
