"""A tenant-scoped app login must not forge or delete worker leases."""
import psycopg
import pytest

from conftest import invoice_pdf
from test_link import send
from finledger_platform import queue
from finledger_platform.db import tenant


def test_app_cannot_update_or_delete_jobs(world, client, conn):
    doc = send(client, world.a['public_token'], ('synthetic.pdf', invoice_pdf(), 'application/pdf')).json()['received'][0]['document_id']
    with tenant(conn, world.a['id']):
        job = conn.execute('select id from jobs where document_id=%s', (doc,)).fetchone()['id']
        for statement in ("update jobs set state='done' where id=%s", 'delete from jobs where id=%s'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute(statement, (job,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("insert into jobs(client_id,document_id,queue,idem_key,state) values (%s,%s,'map','forged','running')", (world.a['id'], doc))
        assert conn.execute('select state from jobs where id=%s', (job,)).fetchone()['state'] == 'queued'


def test_cancel_post_is_tenant_scoped_and_requires_context(world, client, conn):
    doc_a = send(client, world.a['public_token'], ('a.pdf', invoice_pdf(), 'application/pdf')).json()['received'][0]['document_id']
    doc_b = send(client, world.b['public_token'], ('b.pdf', invoice_pdf(), 'application/pdf')).json()['received'][0]['document_id']
    with tenant(conn, world.a['id']):
        queue.enqueue(conn, 'post', world.a['id'], doc_a)
    with tenant(conn, world.b['id']):
        queue.enqueue(conn, 'post', world.b['id'], doc_b)
        assert not conn.execute('select lock_post_jobs_for_edit(%s) as active', (doc_a,)).fetchone()['active']
        assert conn.execute('select cancel_unfinished_post_jobs(%s) as n', (doc_a,)).fetchone()['n'] == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            conn.execute('select cancel_unfinished_post_jobs(%s)', (doc_a,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            conn.execute('select lock_post_jobs_for_edit(%s)', (doc_a,))
    with tenant(conn, world.a['id']):
        assert conn.execute('select cancel_unfinished_post_jobs(%s) as n', (doc_a,)).fetchone()['n'] == 1
        assert conn.execute("select state from jobs where document_id=%s and queue='post'", (doc_a,)).fetchone()['state'] == 'cancelled'
    with tenant(conn, world.b['id']):
        assert conn.execute("select state from jobs where document_id=%s and queue='post'", (doc_b,)).fetchone()['state'] == 'queued'


def test_cancel_invalidates_dead_post_job(world, client, conn, owner):
    doc = send(client, world.a['public_token'], ('synthetic.pdf', invoice_pdf(), 'application/pdf')).json()['received'][0]['document_id']
    with tenant(conn, world.a['id']):
        job = queue.enqueue(conn, 'post', world.a['id'], doc)
    owner.execute("update jobs set state='dead' where id=%s", (job,))
    with tenant(conn, world.a['id']):
        assert not conn.execute('select lock_post_jobs_for_edit(%s) as active', (doc,)).fetchone()['active']
        assert conn.execute('select cancel_unfinished_post_jobs(%s) as n', (doc,)).fetchone()['n'] == 1
        assert conn.execute('select state from jobs where id=%s', (job,)).fetchone()['state'] == 'cancelled'
