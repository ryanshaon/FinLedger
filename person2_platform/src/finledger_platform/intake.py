"""The one intake path every door uses:  blobs -> split -> hash -> store raw -> documents row -> ingest job.

Doors (link, email, CSV, later portal/WhatsApp) differ only in how they authenticate and what goes into
source_meta. Virus scan and file prep happen in the ingest worker, so the vendor gets an answer in milliseconds
and a scanner outage becomes a retry instead of a lost bill.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from . import files
from .queue import enqueue
from .store import object_key


@dataclass
class Receipt:
    received: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)


def _class_for(mime: str) -> str:
    if mime == files.XLSX:
        return "statement"  # vendor statement, not a single bill
    if mime == files.CSV:
        return "csv_row"
    return "invoice"


def store_document(conn: psycopg.Connection, store, client_id: UUID | str, channel: str, part: files.Part,
                   source_meta: dict[str, Any], *, intake_key: str | None = None, po_number: str | None = None,
                   vendor_note: str | None = None, phish_flag: bool = False) -> dict[str, Any]:
    """Persist one document-sized part. Call inside tenant(conn, client_id). Idempotent on intake_key."""
    if intake_key:
        existing = conn.execute(
            "select id, original_filename, content_hash from documents where intake_key = %s", (intake_key,)
        ).fetchone()
        if existing:
            return {"document_id": str(existing["id"]), "filename": existing["original_filename"],
                    "content_hash": existing["content_hash"], "replayed": True}

    doc_id = uuid4()
    now = datetime.now(UTC)
    digest = hashlib.sha256(part.data).hexdigest()
    key = object_key(client_id, doc_id, now, f"raw.{files.EXT[part.mime]}")
    meta = dict(source_meta)
    if part.archive:
        meta["archive"] = part.archive

    store.put(key, part.data, part.mime)
    conn.execute(
        """insert into documents (id, client_id, channel, source_meta, intake_key, object_path, original_filename,
                                  content_hash, mime, size_bytes, document_class, phish_flag, po_number, vendor_note,
                                  received_at, updated_at)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (doc_id, client_id, channel, Jsonb(meta), intake_key, key, files.safe_filename(part.filename, part.mime),
         digest, part.mime, len(part.data), _class_for(part.mime), phish_flag, po_number, vendor_note, now, now),
    )
    conn.execute(
        "insert into document_assets (client_id, document_id, kind, path) values (%s, %s, 'raw', %s)",
        (client_id, doc_id, key),
    )
    enqueue(conn, "ingest", client_id, doc_id)
    return {"document_id": str(doc_id), "filename": part.filename, "content_hash": digest}


def accept(conn: psycopg.Connection, store, client_id: UUID | str, channel: str,
           uploads: list[tuple[str, bytes]], source_meta: dict[str, Any], *, intake_key: str | None = None,
           po_number: str | None = None, vendor_note: str | None = None, phish_flag: bool = False) -> Receipt:
    """Split every upload (ZIP -> members) and store each part as its own document. Call inside tenant()."""
    receipt = Receipt()
    n = 0
    for filename, data in uploads:
        if not data:
            receipt.rejected.append({"filename": filename, "reason": "empty file"})
            continue
        parts, rejected = files.explode(filename, data)
        receipt.rejected += [{"filename": r.filename, "reason": r.reason} for r in rejected]
        for part in parts:
            key = f"{intake_key}#{n}" if intake_key else None
            n += 1
            receipt.received.append(store_document(
                conn, store, client_id, channel, part, source_meta, intake_key=key,
                po_number=po_number, vendor_note=vendor_note, phish_flag=phish_flag))
    return receipt
