"""Index one client's existing books into rag_chunks. Call inside tenant(); embeddings stay null."""
from __future__ import annotations

import json
from typing import Any

from contracts.schemas import CorrectionEvent
from memory.memory_indexer import MemoryIndexer
from rag.chunk_types import ChunkType
from rag.postgres_indexer import PostgresIndexer


def _text(value: Any) -> str:
    """correction_events stores jsonb. The correction model only accepts string aliases."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, separators=(",", ":"))


def _items(conn, kind: str) -> list:
    row = conn.execute(
        "select items from masters_snapshots where kind = %s order by fetched_at desc, id desc limit 1",
        (kind,),
    ).fetchone()
    if not row:
        return []
    items = row["items"]
    if isinstance(items, str):
        items = json.loads(items)
    return items if isinstance(items, list) else []


def index_posted_document(conn, client_id, document_id, erp_id: str | None) -> None:
    """One successfully posted bill. No-op when the ERP id or the invoice is missing."""
    if not erp_id:
        return
    row = conn.execute(
        "select payload from canonical_invoices where document_id = %s", (document_id,)
    ).fetchone()
    if not row:
        return
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        return
    vendor = payload.get("vendor") if isinstance(payload.get("vendor"), dict) else {}
    items = payload.get("line_items") or []
    item = items[0] if items and isinstance(items[0], dict) else {}
    try:
        amount = float(payload.get("total") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    MemoryIndexer(PostgresIndexer(conn)).index_posted_bill(
        str(client_id),
        str(document_id),
        str(erp_id),
        str(vendor.get("gstin") or ""),
        str(item.get("desc") or ""),
        str(item.get("hsn_sac") or ""),
        amount,
        str(payload.get("document_type") or "purchase_invoice"),
    )


def index_client_corpus(conn, client_id) -> None:
    """Latest ledger and vendor masters, every correction, and every posted bill for this client."""
    indexer = PostgresIndexer(conn)
    client = str(client_id)
    for item in _items(conn, "ledger"):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        parent = str(item.get("parent_group") or item.get("parent") or "").strip()
        duty = str(item.get("gst_duty") or "").strip()
        content = f"Ledger {name}. Parent group {parent}."
        metadata: dict[str, Any] = {"name": name, "parent_group": parent}
        if duty:
            content += f" GST duty {duty}."
            metadata["gst_duty"] = duty
        indexer.upsert(client, f"ledger:{name}", ChunkType.LEDGER, content, metadata)
    for item in _items(conn, "vendor"):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        gstin = str(item.get("gstin") or "").strip().upper()
        if not name or not gstin:
            continue
        default_ledger = str(item.get("default_ledger") or "").strip()
        content = f"Vendor {name} GSTIN {gstin}."
        metadata = {"gstin": gstin, "name": name}
        if default_ledger:
            content += f" Default ledger {default_ledger}."
            metadata["default_ledger"] = default_ledger
        indexer.upsert(client, f"vendor:{gstin}", ChunkType.VENDOR, content, metadata)
    memory = MemoryIndexer(indexer)
    rows = conn.execute(
        """select client_id, document_id, vendor_gstin, field, old_value, new_value, hsn, description, actor_id, created_at
           from correction_events order by id"""
    ).fetchall()
    for row in rows:
        memory.index_correction(CorrectionEvent(
            client_id=str(row["client_id"]),
            document_id=str(row["document_id"]),
            vendor_gstin=str(row["vendor_gstin"] or ""),
            field=str(row["field"] or "ledger"),
            hsn_sac=str(row["hsn"] or ""),
            description=str(row["description"] or ""),
            old_ledger=_text(row["old_value"]),
            new_ledger=_text(row["new_value"]),
            actor_id=str(row["actor_id"]) if row["actor_id"] else None,
            created_at=row["created_at"].isoformat(),
        ))
    posted = conn.execute(
        """select document_id, erp_id from posting_attempts
           where result = 'posted' and coalesce(erp_id, '') <> '' order by id"""
    ).fetchall()
    for row in posted:
        index_posted_document(conn, client, row["document_id"], row["erp_id"])
