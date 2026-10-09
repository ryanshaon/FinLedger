"""ingest_worker: virus scan -> file prep -> assets -> documents row -> extract job.

Person 2 owns the documents row until 'extracted'. Outcomes per document:
    virus found        -> status 'quarantined', virus_ok=false, never enqueued to extract
    unreadable file    -> status 'exception' + status_reason (Person 3's exception tray, ask vendor to resend)
    scanner down, I/O  -> job retries with backoff; after max attempts it is 'dead' (poison queue)
    ok                 -> assets written, prepared_at set, extract job with the P2 -> P1 payload
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from . import files, prep, virus
from .config import Settings
from .db import tenant
from .queue import PermanentError, claim, enqueue, finish
from .store import object_key

log = logging.getLogger("finledger.ingest")


def _retry_reason(error: Exception) -> str:
    """Only fixed diagnostic categories may cross the worker boundary."""
    if isinstance(error, virus.ScannerError):
        return "virus scanner unavailable"
    if isinstance(error, psycopg.Error):
        return "ingestion database operation failed"
    if isinstance(error, OSError):
        return "ingestion I/O failed"
    return "ingestion processing failed"


def _unreadable_reason(mime: str) -> str:
    return {
        files.PDF: "unreadable PDF",
        files.JPEG: "unreadable image",
        files.PNG: "unreadable image",
        files.XLSX: "unreadable spreadsheet",
        files.CSV: "unreadable spreadsheet",
    }.get(mime, "unreadable file")


def extract_payload(doc: dict, assets: list[dict]) -> dict[str, Any]:
    """The P2 -> P1 handoff (context/HANDOFFS.md). Paths are object-store keys under client_id/."""
    by_kind = {a["kind"]: a["path"] for a in assets if a["kind"] != "page_image"}
    pages = sorted((a for a in assets if a["kind"] == "page_image"), key=lambda a: a["page_no"])
    return {
        "document_id": str(doc["id"]),
        "client_id": str(doc["client_id"]),
        "source_channel": doc["channel"],
        "document_class": doc["document_class"],
        "mime": doc["mime"],
        "content_hash": doc["content_hash"],
        "raw_path": by_kind["raw"],
        "raw_markdown_path": by_kind.get("markdown"),
        "layout_blocks_path": by_kind.get("layout_json"),
        "page_image_paths": [p["path"] for p in pages],
        "page_count": doc["page_count"],
        "is_scanned": "markdown" not in by_kind,
        "phish_flag": doc["phish_flag"],
        "po_number": doc["po_number"],
    }


def process_ingest(conn: psycopg.Connection, store, settings: Settings, job: dict) -> str:
    client_id, doc_id = job["client_id"], job["document_id"]
    with tenant(conn, client_id):
        doc = conn.execute("select * from documents where id = %s", (doc_id,)).fetchone()
    if doc is None:
        raise PermanentError("document row missing")
    if doc["prepared_at"] is not None or doc["status"] != "received":
        return "skipped"  # already handled (replayed job)

    data = store.get(doc["object_path"])
    if hashlib.sha256(data).hexdigest() != doc["content_hash"]:
        raise RuntimeError("stored bytes do not match content_hash")  # transient store trouble -> retry

    signature = virus.scan(data, settings)  # ScannerError propagates -> retry, file stays unscanned
    if signature:
        with tenant(conn, client_id):
            conn.execute("""update documents set status = 'quarantined', virus_ok = false, status_reason = %s,
                            updated_at = now() where id = %s""", (f"virus: {signature}", doc_id))
        log.warning("quarantined %s: %s", doc_id, signature)
        return "quarantined"

    try:
        prepared = prep.prepare(data, doc["mime"], settings.render_page_cap)
    except PermanentError:
        with tenant(conn, client_id):
            conn.execute("""update documents set status = 'exception', virus_ok = true, status_reason = %s,
                            updated_at = now() where id = %s""", (_unreadable_reason(doc["mime"]), doc_id))
        return "exception"

    # Objects first (idempotent overwrites), then one transaction for rows + the extract job.
    new_assets: list[tuple[str, str, int | None]] = []
    at = doc["received_at"]
    if prepared.markdown.strip():
        key = object_key(client_id, doc_id, at, "markdown.md")
        store.put(key, prepared.markdown.encode(), "text/markdown; charset=utf-8")
        new_assets.append(("markdown", key, None))
    if prepared.layout is not None:
        key = object_key(client_id, doc_id, at, "layout.json")
        store.put(key, json.dumps(prepared.layout, ensure_ascii=False).encode(), "application/json")
        new_assets.append(("layout_json", key, None))
    for page_no, png in sorted(prepared.page_images.items()):
        key = object_key(client_id, doc_id, at, f"pages/p{page_no:03d}.png")
        store.put(key, png, "image/png")
        new_assets.append(("page_image", key, page_no))

    with tenant(conn, client_id):
        for kind, key, page_no in new_assets:
            conn.execute("""insert into document_assets (client_id, document_id, kind, path, page_no)
                            values (%s, %s, %s, %s, %s) on conflict do nothing""",
                         (client_id, doc_id, kind, key, page_no))
        doc = conn.execute("""update documents set virus_ok = true, page_count = %s, prepared_at = now(),
                              updated_at = now() where id = %s returning *""",
                           (prepared.page_count, doc_id)).fetchone()
        assets = conn.execute("select kind, path, page_no from document_assets where document_id = %s",
                              (doc_id,)).fetchall()
        enqueue(conn, "extract", client_id, doc_id, extract_payload(doc, assets))
    return "prepared"


def load_extract_input(conn: psycopg.Connection, store, job: dict) -> dict[str, Any]:
    """Convenience for Person 1's extract worker: the payload plus the markdown text and layout blocks."""
    p = dict(job["payload"])
    p["raw_markdown"] = store.get(p["raw_markdown_path"]).decode() if p["raw_markdown_path"] else ""
    p["layout_blocks"] = json.loads(store.get(p["layout_blocks_path"])) if p["layout_blocks_path"] else None
    return p


def run_once(conn: psycopg.Connection, store, settings: Settings) -> str | None:
    """Keep claim, processing and settlement failures safe for the worker's stderr."""
    try:
        return _run_once(conn, store, settings)
    except Exception:
        # A second DB error must not chain the original sensitive processing exception.
        raise RuntimeError("ingest worker operation failed") from None


def _run_once(conn: psycopg.Connection, store, settings: Settings) -> str | None:
    job = claim(conn, "ingest")
    if job is None:
        return None
    try:
        outcome = process_ingest(conn, store, settings, job)
    except PermanentError:
        finish(conn, job["id"], job["claim_token"], ok=False, error="permanent ingestion failure", permanent=True)
        return "dead"
    except Exception as e:  # scanner down, store hiccup, DB blip: retry, then poison
        reason = _retry_reason(e)
        # Exception messages, chained causes and notes may contain invoice data or credentials.
        log.error("ingest job %s failed: %s", job["id"], reason)
        return finish(conn, job["id"], job["claim_token"], ok=False, error=reason)
    finish(conn, job["id"], job["claim_token"], ok=True)
    return outcome


def run_forever(pool, store, settings: Settings, idle_sleep: float = 1.0) -> None:
    log.info("ingest worker started")
    while True:
        with pool.connection() as conn:
            did = run_once(conn, store, settings)
        if did is None:
            time.sleep(idle_sleep)
