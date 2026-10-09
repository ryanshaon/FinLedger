"""Firm-admin staff invitations: pre-create the users row, invite via Supabase Auth, bind the subject explicitly.

The Supabase secret (service-role) key is accepted only by ``SupabaseAdminClient``; nothing else in the codebase
takes it. Authorization never comes from Auth metadata: the inviter must be a firm admin at AAL2 according to our
own database, and the invited Auth subject is linked to exactly the ``users`` row created by this request.

Supabase Auth and Postgres cannot share a transaction, so the flow is ordered to fail closed:

1. Commit a pending ``users`` row (``auth_subject`` NULL) plus client memberships. An unlinked row cannot sign in:
   ``auth_user_by_subject`` never matches it and there is no fallback by email.
2. Ask Supabase to invite the email. On failure nothing usable exists; the same call can simply be retried.
3. Link the returned subject with a compare-and-set (``auth_subject is null``). If linking fails, delete the new
   Auth user (only when no row is linked to it). If that compensating delete also fails, the orphan Auth identity
   still cannot reach data because it is unlinked; an operator removes it in the Supabase dashboard.

Re-inviting an email whose row is already linked only re-sends the Auth email (Supabase re-sends to users who
have not accepted yet and refuses confirmed ones). It never changes that person's roles, and the returned
subject must equal the stored link.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlsplit
from uuid import UUID

import psycopg

from .auth_gateway import _json_transport
from .db import firm

ROLES = frozenset({"ap_clerk", "approver", "payer"})
MAX_MEMBERSHIPS = 200
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")


class InviteDenied(PermissionError):
    """The caller may not invite staff (not a firm admin, or not AAL2)."""


class InviteInvalid(ValueError):
    """The request is malformed: email, role or client outside the inviter's firm."""


class InviteConflict(ValueError):
    """The email is already provisioned (or used elsewhere); never says which firm."""


class InviteUnavailable(RuntimeError):
    """Supabase Auth or the database failed; no usable account was left behind."""


@dataclass(frozen=True)
class InviteResult:
    user_id: UUID
    auth_subject: UUID
    resent: bool  # True when an earlier pending (unlinked) invite for this email was completed


class SupabaseAdminClient:
    """The only holder of the Supabase secret key. Use only from the invite path, server-side."""

    def __init__(self, base_url: str, secret_key: str,
                 *, transport: Callable[[str, str, dict, dict | None], dict] = _json_transport):
        parts = urlsplit(base_url)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.path not in ("", "/") or parts.query or parts.fragment):
            raise ValueError("Supabase Auth URL must be a project HTTPS origin")
        if not secret_key:
            raise ValueError("Supabase secret key required for invitations")
        self.origin = base_url.rstrip("/")
        self._headers = {"apikey": secret_key, "Authorization": f"Bearer {secret_key}"}
        self._transport = transport

    def __repr__(self) -> str:  # Never let the key reach logs or tracebacks via repr().
        return f"SupabaseAdminClient({self.origin!r})"

    def invite(self, email: str) -> UUID:
        try:
            response = self._transport("POST", f"{self.origin}/auth/v1/invite", dict(self._headers), {"email": email})
            return UUID(response["id"])
        except Exception:
            raise InviteUnavailable("invitation could not be sent") from None

    def delete_user(self, subject: UUID) -> None:
        try:
            self._transport("DELETE", f"{self.origin}/auth/v1/admin/users/{UUID(str(subject))}",
                            dict(self._headers), None)
        except Exception:
            raise InviteUnavailable("could not remove unlinked Auth user") from None


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if len(value) > 254 or not _EMAIL_RE.match(value):
        raise InviteInvalid("invalid email address")
    return value


def _memberships(items: Iterable[tuple[UUID | str, str]]) -> list[tuple[UUID, str]]:
    out: set[tuple[UUID, str]] = set()
    for client_id, role in items:
        if role not in ROLES:
            raise InviteInvalid("invalid role")
        try:
            out.add((UUID(str(client_id)), role))
        except ValueError:
            raise InviteInvalid("invalid client") from None
    if len(out) > MAX_MEMBERSHIPS:
        raise InviteInvalid("too many memberships")
    return sorted(out, key=lambda m: (str(m[0]), m[1]))


class StaffInvitations:
    def __init__(self, admin: SupabaseAdminClient, *, logger: Callable[[str], None] | None = None):
        self._admin = admin
        self._log = logger or (lambda message: None)

    def invite(self, conn: psycopg.Connection, actor, email: str, *, name: str = "", firm_admin: bool = False,
               memberships: Iterable[tuple[UUID | str, str]] = ()) -> InviteResult:
        """``actor`` is a resolved browser ``StaffSession`` (user_id, firm_id, firm_admin, aal)."""
        if getattr(actor, "aal", None) != "aal2" or not getattr(actor, "firm_admin", False):
            raise InviteDenied("firm admin with MFA required")
        email = normalize_email(email)
        name = (name or "").strip()
        if len(name) > 200:
            raise InviteInvalid("name too long")
        if not isinstance(firm_admin, bool):
            raise InviteInvalid("invalid firm_admin flag")
        wanted = _memberships(memberships)

        user_id, resent, linked = self._prepare(conn, actor, email, name, firm_admin, wanted)
        subject = self._admin.invite(email)
        if linked is not None:
            if subject != linked:  # Auth disagrees with our link: grant, change and delete nothing.
                self._log(f"re-invite returned Supabase Auth user {subject}, expected {linked}; check manually")
                raise InviteUnavailable("invitation not linked (identity mismatch)")
            return InviteResult(user_id, subject, True)
        self._link(conn, actor.firm_id, user_id, subject)
        return InviteResult(user_id, subject, resent)

    def _prepare(self, conn, actor, email, name, firm_admin, wanted) -> tuple[UUID, bool, UUID | None]:
        try:
            with firm(conn, actor.firm_id):
                # Re-check from the database, not from the caller's object.
                me = conn.execute("select firm_admin from users where id = %s", (actor.user_id,)).fetchone()
                if not me or not me["firm_admin"]:
                    raise InviteDenied("firm admin with MFA required")
                client_ids = sorted({c for c, _ in wanted}, key=str)
                if client_ids:
                    found = conn.execute("select count(*) as n from clients where id = any(%s) and firm_id = %s",
                                         (client_ids, actor.firm_id)).fetchone()["n"]
                    if found != len(client_ids):
                        raise InviteInvalid("client not in this firm")
                existing = conn.execute("select id, auth_subject, api_token_hash is not null as has_token "
                                        "from users where email = %s for update", (email,)).fetchone()
                if existing and existing["auth_subject"] is None and existing["has_token"]:
                    # A pre-existing API-token account, not a pending invite: never rewrite its roles or link it
                    # through this form. An operator links such accounts explicitly (migration 008 rule).
                    raise InviteConflict("already has an API-token account; ask an operator to link it")
                if existing and existing["auth_subject"] is not None:
                    if wanted or firm_admin or name:
                        raise InviteConflict("already has an account; to re-send, submit the email alone "
                                             "(roles are not changed here)")
                    return existing["id"], True, existing["auth_subject"]
                if existing:
                    user_id, resent = existing["id"], True
                    # A retry describes the whole invitation again: replace, never accumulate, pending grants.
                    conn.execute("update users set name = %s, firm_admin = %s where id = %s and auth_subject is null",
                                 (name, firm_admin, user_id))
                    conn.execute("delete from client_members where user_id = %s", (user_id,))
                else:
                    # api_token_hash stays NULL: invited people use browser sign-in, not bearer tokens.
                    user_id, resent = conn.execute(
                        "insert into users (firm_id, email, name, firm_admin) values (app_firm_id(), %s, %s, %s) "
                        "returning id", (email, name, firm_admin)).fetchone()["id"], False
                for client_id, role in wanted:
                    conn.execute("insert into client_members (client_id, user_id, role) values (%s, %s, %s) "
                                 "on conflict do nothing", (client_id, user_id, role))
                return user_id, resent, None
        except psycopg.errors.UniqueViolation:
            # Same email in another firm (RLS hides it) or a concurrent invite. Do not reveal which.
            raise InviteConflict("email unavailable") from None
        except psycopg.Error:
            raise InviteUnavailable("could not record invitation") from None

    def _link(self, conn, firm_id: UUID, user_id: UUID, subject: UUID) -> None:
        try:
            with firm(conn, firm_id):
                linked = conn.execute("update users set auth_subject = %s where id = %s and auth_subject is null "
                                      "returning id", (subject, user_id)).fetchone()
            if linked:
                return
            reason = "pending user changed"
        except psycopg.Error:
            reason = "database error"
        self._compensate(conn, subject)
        raise InviteUnavailable(f"invitation not linked ({reason})")

    def _compensate(self, conn, subject: UUID) -> None:
        try:
            if conn.execute("select 1 from finledger_private.auth_user_by_subject(%s)", (subject,)).fetchone():
                return  # Linked to someone after all: never delete a live identity.
            self._admin.delete_user(subject)
        except (psycopg.Error, InviteUnavailable):
            # Unlinked, so it cannot sign in to FinLedger; leave a trace for an operator without PII.
            self._log(f"unlinked Supabase Auth user {subject} needs manual removal")
