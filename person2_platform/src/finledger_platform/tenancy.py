"""Client workspaces, tokens, GSTIN checks, and the storage Person 4 writes into (ERP link, mapping, masters, ageing)."""
from __future__ import annotations

import csv
import hashlib
import io
import re
import secrets
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from cryptography.fernet import Fernet
from psycopg.types.json import Jsonb

from . import files

_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z0-9]{10}[0-9A-Z]Z[0-9A-Z]$")
_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
POLICY_FIELDS = ("auto_post_cap", "po_required", "po_required_above", "maker_checker", "tds_sections",
                 "msme_clock", "itc_180_warning", "match_mode")
CSV_MAX_ROWS = 5000


def gstin_valid(gstin: str) -> bool:
    """Format + mod-36 check digit. Used at onboarding (trust boundary); Person 3 may import it for risk marks."""
    g = (gstin or "").strip().upper()
    if not _GSTIN_RE.match(g):
        return False
    total = 0
    for i, ch in enumerate(g[:14]):
        product = _B36.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _B36[(36 - total % 36) % 36] == g[14]


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def new_public_token() -> str:
    return secrets.token_urlsafe(18)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return s if len(s) >= 3 else f"client-{secrets.token_hex(3)}"


# ---------- firms / users (bootstrap runs as owner; everything else under firm/tenant context) ----------

def create_firm_with_admin(owner_conn: psycopg.Connection, firm_name: str, email: str, name: str = "") -> tuple[UUID, UUID, str]:
    """Owner-connection bootstrap. Returns (firm_id, user_id, api_token). The token is shown once."""
    token = "flu_" + secrets.token_urlsafe(32)
    with owner_conn.transaction():
        firm_id = owner_conn.execute("insert into firms (name) values (%s) returning id", (firm_name,)).fetchone()[0]
        user_id = owner_conn.execute(
            "insert into users (firm_id, email, name, firm_admin, api_token_hash) values (%s, %s, %s, true, %s) returning id",
            (firm_id, email.lower(), name, token_hash(token))).fetchone()[0]
    return firm_id, user_id, token


def create_user(conn: psycopg.Connection, email: str, name: str = "", firm_admin: bool = False) -> tuple[UUID, str]:
    """Inside firm(conn, firm_id). Returns (user_id, api_token)."""
    token = "flu_" + secrets.token_urlsafe(32)
    row = conn.execute(
        """insert into users (firm_id, email, name, firm_admin, api_token_hash)
           values (app_firm_id(), %s, %s, %s, %s) returning id""",
        (email.lower(), name, firm_admin, token_hash(token))).fetchone()
    return row["id"], token


# ---------- clients ----------

def create_client(conn: psycopg.Connection, name: str, *, erp_type: str = "tally", gstins: list[str] | None = None,
                  inbound_slug: str | None = None, policy: dict[str, Any] | None = None) -> dict:
    """Inside firm(conn, firm_id). Publishes both doors on creation."""
    gstins = [g.strip().upper() for g in (gstins or [])]
    bad = [g for g in gstins if not gstin_valid(g)]
    if bad:
        raise ValueError(f"invalid GSTIN: {', '.join(bad)}")
    policy = {k: v for k, v in (policy or {}).items() if k in POLICY_FIELDS}
    cols = ["firm_id", "name", "erp_type", "gstins", "public_token", "inbound_slug", *policy]
    vals = [name.strip(), erp_type, gstins, new_public_token(), (inbound_slug or slugify(name)).lower(), *policy.values()]
    return conn.execute(
        f"insert into clients ({', '.join(cols)}) values (app_firm_id(), {', '.join(['%s'] * len(vals))}) returning *",
        vals).fetchone()


def update_policy(conn: psycopg.Connection, client_id: UUID | str, policy: dict[str, Any]) -> dict:
    policy = {k: v for k, v in policy.items() if k in POLICY_FIELDS}
    if not policy:
        return conn.execute("select * from clients where id = %s", (client_id,)).fetchone()
    sets = ", ".join(f"{k} = %s" for k in policy)
    return conn.execute(f"update clients set {sets}, updated_at = now() where id = %s returning *",
                        [*policy.values(), client_id]).fetchone()


def rotate_public_token(conn: psycopg.Connection, client_id: UUID | str) -> str:
    """Old link dies immediately. Vendors holding it get 404 and must use the new one."""
    token = new_public_token()
    conn.execute("update clients set public_token = %s, updated_at = now() where id = %s", (token, client_id))
    return token


def add_member(conn: psycopg.Connection, client_id: UUID | str, user_id: UUID | str, role: str) -> None:
    conn.execute("insert into client_members (client_id, user_id, role) values (%s, %s, %s) on conflict do nothing",
                 (client_id, user_id, role))


# ---------- Person 4 storage ----------

def _fernet(enc_key: str) -> Fernet:
    if not enc_key:
        raise RuntimeError("FINLEDGER_ENC_KEY is not set; refusing to store ERP credentials")
    return Fernet(enc_key.encode())


def pair_erp_agent(conn: psycopg.Connection, client_id: UUID | str, erp_type: str, company_name: str | None = None) -> str:
    """Inside tenant(). Issues a new agent pairing token (shown once); any previous token stops working."""
    token = "fla_" + secrets.token_urlsafe(32)
    conn.execute(
        """insert into erp_links (client_id, erp_type, company_name, agent_token_hash, paired_at)
           values (%s, %s, %s, %s, now())
           on conflict (client_id) do update set erp_type = excluded.erp_type, company_name = excluded.company_name,
                  agent_token_hash = excluded.agent_token_hash, paired_at = now()""",
        (client_id, erp_type, company_name, token_hash(token)))
    return token


def put_erp_credentials(conn: psycopg.Connection, client_id: UUID | str, secret: bytes, enc_key: str) -> None:
    conn.execute("update erp_links set credentials_enc = %s where client_id = %s",
                 (_fernet(enc_key).encrypt(secret), client_id))


def get_erp_credentials(conn: psycopg.Connection, client_id: UUID | str, enc_key: str) -> bytes | None:
    row = conn.execute("select credentials_enc from erp_links where client_id = %s", (client_id,)).fetchone()
    return _fernet(enc_key).decrypt(bytes(row["credentials_enc"])) if row and row["credentials_enc"] else None


def write_masters(conn: psycopg.Connection, client_id: UUID | str, kind: str, items: list[dict],
                  source: str = "tally_agent") -> int:
    """Inside tenant(). Append a snapshot; vendor items with a valid GSTIN also upsert the vendors table."""
    snap_id = conn.execute(
        "insert into masters_snapshots (client_id, kind, items, source) values (%s, %s, %s, %s) returning id",
        (client_id, kind, Jsonb(items), source)).fetchone()["id"]
    if kind == "vendor":
        for v in items:
            gstin = (v.get("gstin") or "").strip().upper()
            if not gstin_valid(gstin) or not v.get("name"):
                continue
            conn.execute(
                """insert into vendors (client_id, gstin, name, pan, state_code, msme)
                   values (%s, %s, %s, %s, %s, %s)
                   on conflict (client_id, gstin) do update set name = excluded.name,
                       pan = coalesce(excluded.pan, vendors.pan), state_code = coalesce(excluded.state_code, vendors.state_code),
                       msme = coalesce(excluded.msme, vendors.msme), updated_at = now()""",
                (client_id, gstin, v["name"], v.get("pan") or gstin[2:12], v.get("state_code") or gstin[:2], v.get("msme")))
    return snap_id


def latest_masters(conn: psycopg.Connection, kind: str) -> dict | None:
    return conn.execute("select * from masters_snapshots where kind = %s order by fetched_at desc, id desc limit 1",
                        (kind,)).fetchone()


def replace_open_bills(conn: psycopg.Connection, client_id: UUID | str, bills: list[dict], as_of: datetime) -> int:
    """Inside tenant(). Person 4's outstanding pull replaces the client's open-bill set atomically."""
    conn.execute("delete from open_bills where client_id = %s", (client_id,))
    with conn.cursor() as cur:
        cur.executemany(
            """insert into open_bills (client_id, party_ledger, vendor_gstin, bill_ref, bill_date, due_date, amount,
                                       pending_amount, as_of) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(client_id, b["party_ledger"], b.get("vendor_gstin"), b["bill_ref"], b.get("bill_date"), b.get("due_date"),
              b["amount"], b["pending_amount"], as_of) for b in bills])
    return len(bills)


def upsert_mapping(conn: psycopg.Connection, client_id: UUID | str, kind: str, our_key: str, erp_value: str) -> None:
    conn.execute(
        """insert into mapping_table (client_id, kind, our_key, erp_value) values (%s, %s, %s, %s)
           on conflict (client_id, kind, our_key) do update set erp_value = excluded.erp_value, updated_at = now()""",
        (client_id, kind, our_key, erp_value))


# ---------- CSV door (onboarding / migration stub) ----------

def csv_rows_as_parts(filename: str, data: bytes) -> list[files.Part]:
    """One bill per CSV row. Each row keeps the header so it is self-describing for Person 1."""
    text = data.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        raise ValueError("CSV needs a header row and at least one bill row")
    if len(rows) - 1 > CSV_MAX_ROWS:
        raise ValueError(f"CSV has more than {CSV_MAX_ROWS} rows")
    header, parts = rows[0], []
    stem = filename.rsplit(".", 1)[0] or "bills"
    for n, row in enumerate(rows[1:], start=1):
        if not any(c.strip() for c in row):
            continue
        out = io.StringIO()
        csv.writer(out).writerows([header, row])
        parts.append(files.Part(f"{stem}-row{n}.csv", out.getvalue().encode(), files.CSV))
    return parts
