from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from psycopg.rows import dict_row

from finledger_platform import tenancy
from finledger_platform.db import firm, tenant
from rag.postgres_indexer import PostgresIndexer
from rag.retriever import Retriever
from usage.kill_switch import TokenCapExceededException
from usage.model_router import ModelRouter, Settings
from usage.postgres_kill_switch import PostgresKillSwitch
from usage.postgres_usage_writer import PostgresUsageWriter
from usage.provider_errors import ProviderTransientError
from usage.providers.gemini import GeminiProvider, GeminiSettings, GeminiTariff
from usage.providers.http_transport import HttpGeminiTransport
from usage.providers.transport import FakeGeminiTransport, TransportError, TransportResponse
from testing.pg_harness import ai_app_dsn, ai_owner_dsn


def _seed_client(owner_dsn: str) -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:8]
    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        firm_id, _, _ = tenancy.create_firm_with_admin(owner, f"AI Firm {suffix}", f"ai-{suffix}@test.example")
        with firm(owner, firm_id) as conn:
            conn.row_factory = dict_row
            client = tenancy.create_client(
                conn,
                f"Client A {suffix}",
                gstins=["27AAPFU0939F1ZV"],
                inbound_slug=f"client-{suffix}",
            )
            client_id = str(client["id"])
            doc_id = str(
                conn.execute(
                    """insert into documents (client_id, channel, object_path, content_hash, mime, size_bytes, document_class)
                       values (%s::uuid, 'portal', %s, repeat('a', 64), 'application/pdf', 10, 'invoice')
                       returning id""",
                    (client_id, f"{client_id}/raw/test.pdf"),
                ).fetchone()["id"]
            )
    return str(firm_id), client_id, doc_id


def _bill_rows(dsn: str, client_id: str) -> list[dict]:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with tenant(conn, client_id):
            return list(conn.execute(
                """select u.reservation_id::text as reservation_id, u.tokens_in, u.tokens_out, u.cost_usd, u.purpose,
                          r.state
                   from llm_usage_rows u
                   join llm_token_reservations r on r.reservation_id = u.reservation_id
                   where u.client_id = %s::uuid
                   order by u.id""",
                (client_id,),
            ).fetchall())


def _holds(dsn: str, client_id: str) -> list[dict]:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with tenant(conn, client_id):
            return list(conn.execute(
                """select reservation_id::text as reservation_id, state
                   from llm_token_reservations
                   where client_id = %s::uuid
                   order by created_at""",
                (client_id,),
            ).fetchall())


def _assert_app_worker(dsn: str) -> None:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute(
            """select session_user as role_name, r.rolsuper, r.rolbypassrls,
                      pg_has_role(session_user, 'finledger_app', 'member') as app_member,
                      pg_has_role(session_user, 'finledger_worker_extract', 'member') as worker_member
               from pg_roles r where r.rolname = session_user"""
        ).fetchone()
    assert row["role_name"] == "fl_app"
    assert row["rolsuper"] is False and row["rolbypassrls"] is False
    assert row["app_member"] is True and row["worker_member"] is True


class _ScriptedHttp:
    def __init__(self, results):
        self._results = list(results)

    def __call__(self, url, headers, body, timeout):
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _gemini_body(text: str, usage: dict | None) -> bytes:
    import json

    payload: dict = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    if usage is not None:
        payload["usageMetadata"] = usage
    return json.dumps(payload).encode()


def _durable_gemini(conn, transport, *, priced: bool, max_retries: int = 0) -> GeminiProvider:
    kwargs = {"sleeper": lambda _delay: None}
    if priced:
        kwargs["tariff"] = GeminiTariff(input_usd_per_million=2, output_usd_per_million=4)
    return GeminiProvider(
        GeminiSettings(model="gemini-1.5-flash", location="asia-south1", max_retries=max_retries),
        transport,
        PostgresKillSwitch(conn, limit=50_000),
        **kwargs,
    )


def test_restart_persists_chunks_and_usage(ai_owner_dsn, ai_app_dsn):
    _, client_id, doc_id = _seed_client(ai_owner_dsn)
    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
        with tenant(conn, client_id):
            indexer = PostgresIndexer(conn)
            indexer.upsert(client_id, "memory:1", "memory", "vendor gstin memory", {"gstin": "27AAPFU0939F1ZV"})
        switch = PostgresKillSwitch(conn, limit=20_000)
        reservation = switch.reserve(client_id, doc_id, 15)
        assert switch.settle_usage(
            reservation, purpose="extract", model="mock", tokens_in=10, tokens_out=5, latency_ms=1, cost_usd=0.0,
        )
    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn2:
        with tenant(conn2, client_id):
            chunks = PostgresIndexer(conn2).get_namespace_chunks(client_id)
            assert len(chunks) == 1
            usage_count = conn2.execute(
                "select count(*) from llm_usage_rows where client_id = %s::uuid", (client_id,)
            ).fetchone()["count"]
            assert usage_count == 1
            ranked = Retriever(PostgresIndexer(conn2)).retrieve(client_id, "27AAPFU0939F1ZV", "", [], "", "0", "invoice")
            assert ranked
    rows = _bill_rows(ai_app_dsn, client_id)
    assert len(rows) == 1
    assert rows[0]["reservation_id"] == reservation.reservation_id
    assert rows[0]["state"] == "reconciled"
    assert rows[0]["tokens_in"] == 10 and rows[0]["tokens_out"] == 5


def test_cross_tenant_denial(ai_owner_dsn, ai_app_dsn):
    _assert_app_worker(ai_app_dsn)
    _, client_a, doc_a = _seed_client(ai_owner_dsn)
    _, client_b, _ = _seed_client(ai_owner_dsn)
    with psycopg.connect(ai_app_dsn) as conn:
        with tenant(conn, client_a):
            PostgresIndexer(conn).upsert(client_a, "memory:1", "memory", "secret-a", {})
        switch = PostgresKillSwitch(conn, limit=20_000)
        reservation = switch.reserve(client_a, doc_a, 2)
        switch.settle_usage(
            reservation, purpose="extract", model="mock", tokens_in=1, tokens_out=1, latency_ms=1, cost_usd=0.0,
        )
    with psycopg.connect(ai_app_dsn) as conn:
        with tenant(conn, client_b):
            assert PostgresIndexer(conn).get_namespace_chunks(client_b) == []
            count = conn.execute("select count(*) from llm_usage_rows").fetchone()["count"]
            assert count == 0
        with pytest.raises(psycopg.Error, match="unknown_reservation"):
            with tenant(conn, client_b):
                conn.execute("select llm_reconcile_tokens(%s::uuid)", (reservation.reservation_id,))


def test_concurrent_reservations_enforce_cap(ai_owner_dsn, ai_app_dsn):
    _, client_id, doc_id = _seed_client(ai_owner_dsn)

    def attempt():
        with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
            with tenant(conn, client_id):
                switch = PostgresKillSwitch(conn, limit=20, enabled=True)
                try:
                    switch.reserve(client_id, doc_id, 15)
                    return "ok"
                except TokenCapExceededException:
                    return "cap"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert results.count("ok") == 1
    assert results.count("cap") == 1


def test_kill_switch_disabled_skips_reserve(ai_owner_dsn, ai_app_dsn):
    _, client_id, doc_id = _seed_client(ai_owner_dsn)
    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
        with tenant(conn, client_id):
            switch = PostgresKillSwitch(conn, limit=1, enabled=False)
            reservation = switch.reserve(client_id, doc_id, 10_000)
            switch.reconcile(reservation, 10_000)
            held = conn.execute(
                "select count(*) from llm_token_reservations where state = 'held'"
            ).fetchone()["count"]
            assert held == 0


def test_writer_requires_the_hold_id_and_stores_unpriced_as_null(ai_owner_dsn, ai_app_dsn):
    _, client_id, doc_id = _seed_client(ai_owner_dsn)
    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
        switch = PostgresKillSwitch(conn, limit=1000)
        reservation = switch.reserve(client_id, doc_id, 10)
        priced = switch.reserve(client_id, doc_id, 10)
        writer = PostgresUsageWriter(conn)
        with pytest.raises(ValueError, match="reservation_id"):
            writer.log_usage(client_id, doc_id, "mock", 1, 1, 1, 0.0, "extract")
        writer.log_usage(
            client_id, doc_id, "mock", 1, 1, 1, None, "extract", reservation_id=reservation.reservation_id,
        )
    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
        PostgresUsageWriter(conn).log_usage(
            client_id, doc_id, "mock", 4, 6, 1, 0.0, "extract", reservation_id=priced.reservation_id,
        )
    rows = _bill_rows(ai_app_dsn, client_id)
    assert [r["reservation_id"] for r in rows] == [reservation.reservation_id, priced.reservation_id]
    assert all(r["state"] == "reconciled" for r in rows)
    assert rows[0]["cost_usd"] is None  # unknown, never 0 (migration 014)
    assert rows[1]["tokens_in"] == 4 and rows[1]["tokens_out"] == 6 and rows[1]["cost_usd"] == 0
    assert _holds(ai_app_dsn, client_id) == [
        {"reservation_id": reservation.reservation_id, "state": "reconciled"},
        {"reservation_id": priced.reservation_id, "state": "reconciled"},
    ]


def test_priced_thoughts_settle_on_the_hold_for_a_second_connection(ai_owner_dsn, ai_app_dsn):
    _, client_id, doc_id = _seed_client(ai_owner_dsn)
    http = _ScriptedHttp([(200, _gemini_body("{}", {
        "promptTokenCount": 3, "candidatesTokenCount": 4, "thoughtsTokenCount": 1000, "totalTokenCount": 1007,
    }))])
    transport = HttpGeminiTransport(
        credential="test-credential-do-not-log", project="finledger-test", http_post=http, timeout_seconds=5,
    )
    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
        response = _durable_gemini(conn, transport, priced=True).complete(
            "extract", "gemini-1.5-flash", client_id, doc_id, {"markdown": "x"},
        )
    assert response.tokens_in == 3 and response.tokens_out == 1004
    assert response.usage_persisted is True and response.cost_usd > 0
    rows = _bill_rows(ai_app_dsn, client_id)
    assert len(rows) == 1
    assert rows[0]["reservation_id"] == response.reservation_id
    assert rows[0]["tokens_in"] == 3 and rows[0]["tokens_out"] == 1004
    assert rows[0]["state"] == "reconciled"
    assert float(rows[0]["cost_usd"]) == pytest.approx(response.cost_usd)
    assert float(rows[0]["cost_usd"]) > 0


def test_unknown_usage_reconciles_at_the_reservation_not_zero(ai_owner_dsn, ai_app_dsn):
    _, client_id, doc_id = _seed_client(ai_owner_dsn)
    http = _ScriptedHttp([(200, _gemini_body("kept", None))])
    transport = HttpGeminiTransport(
        credential="test-credential-do-not-log", project="finledger-test", http_post=http, timeout_seconds=5,
    )
    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
        response = _durable_gemini(conn, transport, priced=True).complete(
            "extract", "gemini-1.5-flash", client_id, doc_id, {"markdown": "x"},
        )
    assert response.text == "kept"
    assert response.usage_known is False
    assert response.tokens_in > 0 and response.tokens_out == 256
    rows = _bill_rows(ai_app_dsn, client_id)
    assert len(rows) == 1
    assert rows[0]["reservation_id"] == response.reservation_id
    assert rows[0]["tokens_in"] == response.tokens_in and rows[0]["tokens_out"] == 256
    assert rows[0]["state"] == "reconciled"
    assert float(rows[0]["cost_usd"]) > 0


def test_unpriced_gemini_stores_null_cost_and_keeps_its_tokens(ai_owner_dsn, ai_app_dsn):
    _, client_id, doc_id = _seed_client(ai_owner_dsn)
    transport = FakeGeminiTransport([TransportResponse("{}", 3, 4, 1)])
    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
        response = _durable_gemini(conn, transport, priced=False).complete(
            "extract", "gemini-1.5-flash", client_id, doc_id, {"markdown": "x"},
        )
    assert response.cost_computed is False and response.usage_persisted is True
    rows = _bill_rows(ai_app_dsn, client_id)
    assert len(rows) == 1 and rows[0]["reservation_id"] == response.reservation_id
    assert rows[0]["cost_usd"] is None and rows[0]["state"] == "reconciled"
    assert rows[0]["tokens_in"] == 3 and rows[0]["tokens_out"] == 4  # still counted toward the cap


def test_extract_worker_does_not_insert_a_second_usage_row(ai_owner_dsn, ai_app_dsn):
    from contracts.schemas import CanonicalInvoice, LineItem, Vendor
    from extract.extract_worker import ExtractWorker

    _, client_id, doc_id = _seed_client(ai_owner_dsn)
    invoice = CanonicalInvoice(
        document_id=doc_id, document_type="purchase_invoice",
        vendor=Vendor(name="Acme", gstin="29ABCDE1234F1Z5", pan="ABCDE1234F", state="KA"),
        invoice_no="INV-1", invoice_date="2026-09-01", total=118.0, taxable=100.0,
        line_items=[LineItem(desc="Steel", hsn_sac="7208", qty=1, rate=100, taxable=100, tax_rate=18, tax_amount=18)],
    )
    transport = FakeGeminiTransport([TransportResponse(invoice.model_dump_json(), 3, 4, 2)])

    class Router:
        def __init__(self, provider):
            self.provider = provider

        def resolve_model(self, _purpose):
            return "gemini-1.5-flash"

    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
        provider = _durable_gemini(conn, transport, priced=True)
        worker = ExtractWorker(PostgresUsageWriter(conn))
        worker.router = Router(provider)
        parsed = worker.extract(client_id, doc_id, "Invoice No: INV-1\nTotal: 118")
    assert parsed.invoice_no == "INV-1"
    rows = _bill_rows(ai_app_dsn, client_id)
    assert len(rows) == 1
    assert rows[0]["tokens_in"] == 3 and rows[0]["tokens_out"] == 4
    assert rows[0]["state"] == "reconciled"
    assert float(rows[0]["cost_usd"]) > 0


def test_failed_settle_leaves_the_hold_and_keeps_a_committed_row(ai_owner_dsn, ai_app_dsn):
    _, client_id, doc_id = _seed_client(ai_owner_dsn)
    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
        switch = PostgresKillSwitch(conn, limit=20_000)
        first = switch.reserve(client_id, doc_id, 30)
        with pytest.raises(psycopg.Error):
            switch.settle_usage(
                first, purpose="not-a-purpose", model="gemini-1.5-flash",
                tokens_in=3, tokens_out=4, latency_ms=1, cost_usd=0.01,
            )
        assert _holds(ai_app_dsn, client_id) == [{"reservation_id": first.reservation_id, "state": "held"}]
        assert _bill_rows(ai_app_dsn, client_id) == []
        assert switch.settle_usage(
            first, purpose="extract", model="gemini-1.5-flash",
            tokens_in=3, tokens_out=4, latency_ms=1, cost_usd=0.01,
        )
        second = switch.reserve(client_id, doc_id, 30)
        with pytest.raises(psycopg.Error):
            switch.settle_usage(
                second, purpose="not-a-purpose", model="gemini-1.5-flash",
                tokens_in=1, tokens_out=1, latency_ms=1, cost_usd=0.02,
            )
    rows = _bill_rows(ai_app_dsn, client_id)
    assert [row["reservation_id"] for row in rows] == [first.reservation_id]
    assert rows[0]["state"] == "reconciled"
    holds = {row["reservation_id"]: row["state"] for row in _holds(ai_app_dsn, client_id)}
    assert holds[first.reservation_id] == "reconciled"
    assert holds[second.reservation_id] == "held"
    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
        switch = PostgresKillSwitch(conn, limit=20_000)
        assert switch.settle_usage(
            second, purpose="extract", model="gemini-1.5-flash",
            tokens_in=1, tokens_out=1, latency_ms=1, cost_usd=0.02,
        )
    kept = {row["reservation_id"] for row in _bill_rows(ai_app_dsn, client_id)}
    assert kept == {first.reservation_id, second.reservation_id}


def test_timeout_without_a_body_is_released(ai_owner_dsn, ai_app_dsn):
    _, client_id, doc_id = _seed_client(ai_owner_dsn)
    transport = FakeGeminiTransport([TransportError("timeout")])
    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
        provider = _durable_gemini(conn, transport, priced=True, max_retries=0)
        with pytest.raises(ProviderTransientError):
            provider.complete("extract", "gemini-1.5-flash", client_id, doc_id, {"markdown": "x"})
    assert _bill_rows(ai_app_dsn, client_id) == []
    holds = _holds(ai_app_dsn, client_id)
    assert len(holds) == 1 and holds[0]["state"] == "released"


def test_model_router_accepts_postgres_switch_and_refuses_hosted(ai_owner_dsn, ai_app_dsn, monkeypatch):
    with psycopg.connect(ai_app_dsn, row_factory=dict_row) as conn:
        switch = PostgresKillSwitch(conn, limit=20_000)
        router = ModelRouter(Settings(provider="mock"), kill_switch=switch)
        assert router.provider.kill_switch is switch
        monkeypatch.setenv("FINLEDGER_ENV", "staging")
        with pytest.raises(RuntimeError, match="mock LLM provider"):
            ModelRouter(Settings(provider="mock"), kill_switch=switch)
        monkeypatch.setenv("FINLEDGER_ENV", "production")
        with pytest.raises(RuntimeError, match="gemini LLM provider"):
            ModelRouter(
                Settings(provider="gemini", gemini_location="asia-south1"),
                gemini_transport=FakeGeminiTransport(),
                kill_switch=switch,
            )


def test_default_extract_is_durable_only_with_a_connection():
    import inspect

    from finledger_platform.extract_worker import default_extract

    source = inspect.getsource(default_extract)
    assert "ExtractWorker(UsageWriter())" in source  # no connection: in-memory, for local tools and tests
    assert "PostgresKillSwitch(conn" in source and "PostgresUsageWriter(conn)" in source
    assert "kill_switch=switch" in source
