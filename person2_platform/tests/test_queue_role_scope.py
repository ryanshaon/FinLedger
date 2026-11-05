"""Queue privileges must follow the actual session login, not the requested queue."""
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from conftest import invoice_pdf
from test_link import send
from finledger_platform import queue
from finledger_platform.db import tenant


def test_queue_roles_cannot_claim_or_finish_another_queue(owner_dsn, world, client, conn):
    info = conninfo_to_dict(owner_dsn)
    assert info.get('host') in ('127.0.0.1', 'localhost', '::1'), 'only a disposable local database is allowed'
    suffix = uuid4().hex
    roles = {kind: 'zz_q_' + kind + '_' + suffix for kind in ('map', 'extract', 'plain')}
    doc = send(client, world.a['public_token'], ('synthetic.pdf', invoice_pdf(), 'application/pdf')).json()['received'][0]['document_id']
    with tenant(conn, world.a['id']):
        map_id = queue.enqueue(conn, 'map', world.a['id'], doc)
        extract_id = queue.enqueue(conn, 'extract', world.a['id'], doc)
    with psycopg.connect(owner_dsn, autocommit=True) as owner:
        created = []
        try:
            for kind, name in roles.items():
                owner.execute(sql.SQL('create role {} login password {}').format(sql.Identifier(name), sql.Literal('temporary-test-password')))
                created.append(name)
                owner.execute(sql.SQL('grant finledger_app to {}').format(sql.Identifier(name)))
                if kind != 'plain':
                    owner.execute(sql.SQL('grant {} to {}').format(sql.Identifier('finledger_worker_' + kind), sql.Identifier(name)))
            def connect(kind):
                return psycopg.connect(make_conninfo(owner_dsn, user=roles[kind], password='temporary-test-password'), autocommit=True, row_factory=dict_row)
            with connect('map') as mapper, connect('extract') as extractor, connect('plain') as plain:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    queue.claim(mapper, 'extract')
                assert owner.execute('select state,attempts,claim_token from jobs where id=%s', (extract_id,)).fetchone() == ('queued', 0, None)
                claimed = queue.claim(mapper, 'map')
                assert claimed['id'] == map_id
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    queue.finish(extractor, map_id, claimed['claim_token'], ok=True)
                assert owner.execute('select state,attempts,claim_token from jobs where id=%s', (map_id,)).fetchone() == ('running', 1, claimed['claim_token'])
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    queue.claim(plain, 'map')
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    queue.finish(plain, map_id, claimed['claim_token'], ok=True)
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    plain.execute('select requeue_dead_job(%s)', (map_id,))
                assert queue.finish(mapper, map_id, claimed['claim_token'], ok=True) == 'done'
                extracted = queue.claim(extractor, 'extract')
                assert extracted['id'] == extract_id
                assert queue.finish(extractor, extract_id, extracted['claim_token'], ok=True) == 'done'
        finally:
            for name in created:
                owner.execute(sql.SQL('drop role {}').format(sql.Identifier(name)))
