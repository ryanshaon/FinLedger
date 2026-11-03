import pytest
from finledger_control.service import ControlService, Forbidden
from test_service import canonical, draft


def test_replayed_approval_requires_approver_role(conn, world, document):
    cid = world["client"]["id"]
    svc = ControlService(conn)
    svc.score(cid, document, canonical(document), world["clerk"])
    routed = svc.submit_draft(cid, document, draft(document), world["clerk"])
    svc.approve(cid, document, world["approver"], post=False, expected_revision=routed["revision"])
    with pytest.raises(Forbidden):
        svc.approve(cid, document, world["payer"], post=True, expected_revision=routed["revision"])
    assert svc.post_job_count(cid, document) == 0
    assert svc.approve(cid, document, world["approver"], post=False, expected_revision=routed["revision"])["status"] == "approved"
