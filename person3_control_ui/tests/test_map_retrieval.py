"""Postgres RLS: a map trace sees only the caller's chunks, and extract still has no chunks."""
from __future__ import annotations

import hashlib
import json
import uuid
import psycopg

from extract.extract_worker import ExtractWorker
from finledger_control.service import ControlService
from finledger_platform.db import firm, tenant
from finledger_platform.map_worker import default_map, run_once
from finledger_platform.queue import enqueue
from finledger_platform.tenancy import create_client
from rag.postgres_indexer import PostgresIndexer
from test_service import canonical
from usage.provider import ProviderResponse
from usage.usage_writer import UsageWriter

MARKER_A = "client A private marker"
MARKER_B = "client B private marker"
GSTIN = "27AAACS1234A1Z2"


def _document(conn, client_id, status="extracted"):
    doc_id = str(uuid.uuid4())
    digest = hashlib.sha256(doc_id.encode()).hexdigest()
    with tenant(conn, client_id):
        conn.execute(
            """insert into documents (id, client_id, channel, object_path, content_hash, mime, size_bytes, status)
               values (%s, %s, 'portal', %s, %s, 'application/pdf', 10, %s)""",
            (doc_id, client_id, f"{client_id}/2026/10/{doc_id}/raw.pdf", digest, status),
        )
    return doc_id


def _invoice(doc_id, number):
    invoice = canonical(doc_id, invoice_no=number)
    invoice["line_items"][0]["desc"] = "Widget calibration alpha"
    invoice["line_items"][0]["hsn_sac"] = "9983"
    return invoice


def _trace(conn, client_id, doc_id):
    with tenant(conn, client_id):
        row = conn.execute(
            """select metadata from workflow_events
               where document_id = %s and action = 'map_trace' order by id desc limit 1""",
            (doc_id,),
        ).fetchone()
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return metadata["retrieved_chunk_ids"]


def _chunks(conn, client_id):
    with tenant(conn, client_id):
        return PostgresIndexer(conn).get_namespace_chunks(str(client_id))


def test_map_trace_lists_only_this_client_and_extract_has_no_chunks(conn, pool, world, owner_dsn):
    client_a = world["client"]["id"]
    with firm(conn, world["client"]["firm_id"]):
        other = create_client(conn, "Other Steel", inbound_slug="other-steel")
    client_b = other["id"]
    with tenant(conn, client_a):
        PostgresIndexer(conn).upsert(str(client_a), "memory:a-marker", "memory", MARKER_A, {"gstin": GSTIN})
    with tenant(conn, client_b):
        PostgresIndexer(conn).upsert(str(client_b), "memory:b-marker", "memory", MARKER_B, {"gstin": GSTIN})

    captured = {}

    class Router:
        def __init__(self):
            self.provider = self

        def resolve_model(self, purpose):
            return "mock-extract"

        def complete(self, purpose, model, client_id, document_id, payload):
            captured["purpose"] = purpose
            captured["payload"] = payload
            return ProviderResponse(json.dumps({
                "document_id": document_id,
                "document_type": "purchase_invoice",
                "vendor": {"name": "Shree Ganesh Metals", "gstin": GSTIN},
                "invoice_no": "EX-1",
                "invoice_date": "2026-10-01",
                "total": 1,
            }), 1, 1, 1, 0.0)

    worker = ExtractWorker(UsageWriter())
    worker.router = Router()
    worker.extract(str(client_a), str(uuid.uuid4()), "Invoice No: EX-1\nTotal: 1")
    assert captured["purpose"] == "extract"
    assert "chunks" not in captured["payload"]
    assert MARKER_A not in json.dumps(captured["payload"])
    assert MARKER_B not in json.dumps(captured["payload"])

    doc_id = _document(conn, client_a)
    ControlService(conn).score(client_a, doc_id, _invoice(doc_id, "INV-MAP-1"), world["clerk"])
    with tenant(conn, client_a):
        enqueue(conn, "map", client_a, doc_id, {"document_id": str(doc_id), "client_id": str(client_a)})
    assert run_once(conn, default_map(conn)) == "mapped"
    first = _trace(conn, client_a, doc_id)
    ids_a = {chunk["chunk_id"] for chunk in _chunks(conn, client_a)}
    ids_b = {chunk["chunk_id"] for chunk in _chunks(conn, client_b)}
    assert "memory:a-marker" in first
    assert "memory:b-marker" not in first
    assert set(first) <= ids_a
    assert "memory:b-marker" in ids_b - ids_a
    with tenant(conn, client_a):
        embedding = conn.execute(
            "select embedding from rag_chunks where ref = 'memory:a-marker'"
        ).fetchone()["embedding"]
    assert embedding is None

    with pool.connection() as restarted:
        assert any(chunk["chunk_id"] == "memory:a-marker" for chunk in _chunks(restarted, client_a))
        assert all(chunk["chunk_id"] != "memory:b-marker" for chunk in _chunks(restarted, client_a))

    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        owner.execute(
            "update jobs set state = 'queued', claim_token = null, run_after = now() where queue = 'map' and document_id = %s",
            (doc_id,),
        )
    assert run_once(conn, default_map(conn)) == "skipped"

    ControlService(conn).edit_field(
        client_a, doc_id, world["clerk"], "party_ledger", "Ganesh Metals - AP", expected_revision=1,
    )
    second_id = _document(conn, client_a)
    ControlService(conn).score(client_a, second_id, _invoice(second_id, "INV-MAP-2"), world["clerk"])
    with tenant(conn, client_a):
        enqueue(conn, "map", client_a, second_id, {"document_id": str(second_id), "client_id": str(client_a)})
    assert run_once(conn, default_map(conn)) == "mapped"
    second = _trace(conn, client_a, second_id)
    with tenant(conn, client_a):
        correction = conn.execute(
            "select ref from rag_chunks where chunk_type = 'memory' and content like %s",
            ("%Ganesh Metals - AP%",),
        ).fetchone()
    assert correction is not None
    assert second[0] == correction["ref"]
    assert "memory:b-marker" not in second
    assert set(second) <= {chunk["chunk_id"] for chunk in _chunks(conn, client_a)}
    with tenant(conn, client_a):
        draft_a = conn.execute(
            "select payload from voucher_drafts where document_id = %s", (second_id,)
        ).fetchone()["payload"]
        trace_a = conn.execute(
            """select metadata from workflow_events
               where document_id = %s and action = 'map_trace' order by id desc limit 1""",
            (second_id,),
        ).fetchone()["metadata"]
    if isinstance(draft_a, str):
        draft_a = json.loads(draft_a)
    if isinstance(trace_a, str):
        trace_a = json.loads(trace_a)
    assert draft_a["party_ledger"] == "Ganesh Metals - AP"
    assert trace_a["party_ledger_from_memory"] == "Ganesh Metals - AP"
    assert trace_a["party_ledger_chunk_id"] == correction["ref"]

    doc_b = _document(conn, client_b)
    ControlService(conn).score(client_b, doc_b, _invoice(doc_b, "INV-MAP-B"), world["clerk"])
    with tenant(conn, client_b):
        enqueue(conn, "map", client_b, doc_b, {"document_id": str(doc_b), "client_id": str(client_b)})
    assert run_once(conn, default_map(conn)) == "mapped"
    trace_b = _trace(conn, client_b, doc_b)
    with tenant(conn, client_b):
        draft_b = conn.execute(
            "select payload from voucher_drafts where document_id = %s", (doc_b,)
        ).fetchone()["payload"]
        meta_b = conn.execute(
            """select metadata from workflow_events
               where document_id = %s and action = 'map_trace' order by id desc limit 1""",
            (doc_b,),
        ).fetchone()["metadata"]
    if isinstance(draft_b, str):
        draft_b = json.loads(draft_b)
    if isinstance(meta_b, str):
        meta_b = json.loads(meta_b)
    assert correction["ref"] not in trace_b
    assert "memory:a-marker" not in trace_b
    assert draft_b["party_ledger"] == "Shree Ganesh Metals"
    assert meta_b["party_ledger_from_memory"] is None
    assert meta_b["party_ledger_chunk_id"] is None
    assert set(trace_b) <= {chunk["chunk_id"] for chunk in _chunks(conn, client_b)}
