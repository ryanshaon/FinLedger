"""Platform score worker driving the real ControlService.score, as a login holding only the score worker role."""
from __future__ import annotations

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from conftest import APP_PASSWORD
from finledger_platform.db import tenant
from finledger_platform.score_worker import default_score, run_once
from test_service import canonical


@pytest.fixture
def score_conn(owner_dsn, app_dsn):
    with psycopg.connect(owner_dsn, autocommit=True) as c:
        c.execute("drop role if exists fl_score")
        c.execute(f"create role fl_score login password '{APP_PASSWORD}' in role finledger_app, finledger_worker_score")
    info = conninfo_to_dict(owner_dsn); info.update(user="fl_score", password=APP_PASSWORD)
    with psycopg.connect(make_conninfo(**info), autocommit=True, row_factory=dict_row) as conn:
        yield conn


def queue_score(owner_dsn, client_id, document_id, invoice):
    with psycopg.connect(owner_dsn, autocommit=True) as c:
        c.execute("""insert into jobs (client_id, document_id, queue, payload) values (%s, %s, 'score', %s)""",
                  (client_id, document_id, Jsonb({"client_id": str(client_id), "document_id": document_id,
                                                  "maker_id": None, "canonical_invoice": invoice})))
        return c.execute("select u.id from users u join clients c on c.firm_id = u.firm_id where c.id = %s and u.automation",
                         (client_id,)).fetchone()[0]


def test_score_worker_scores_as_the_automation_maker(score_conn, owner_dsn, world, document):
    cid = world["client"]["id"]
    maker = queue_score(owner_dsn, cid, document, canonical(document))
    assert run_once(score_conn, default_score()) == "scored"
    with tenant(score_conn, cid):
        assert score_conn.execute("select status from documents where id = %s", (document,)).fetchone()["status"] == "scored"
        assert score_conn.execute("select maker_id from canonical_invoices where document_id = %s",
                                  (document,)).fetchone()["maker_id"] == maker
        assert score_conn.execute("select count(*) from risk_assessments where document_id = %s",
                                  (document,)).fetchone()["count"] == 1
        assert score_conn.execute("select state from jobs where queue = 'score'").fetchone()["state"] == "done"


def test_invalid_invoice_becomes_an_exception_without_partial_rows(score_conn, owner_dsn, world, document):
    cid = world["client"]["id"]
    queue_score(owner_dsn, cid, document, canonical(document, line_items=[], total=0))
    assert run_once(score_conn, default_score()) == "exception"
    with tenant(score_conn, cid):
        row = score_conn.execute("select status, status_reason from documents where id = %s", (document,)).fetchone()
        assert row == {"status": "exception", "status_reason": "canonical_invoice_rejected"}
        assert score_conn.execute("select count(*) from canonical_invoices").fetchone()["count"] == 0
        assert score_conn.execute("select last_error from jobs where queue = 'score'").fetchone()["last_error"] is None
