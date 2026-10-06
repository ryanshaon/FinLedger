import pytest

from finledger_control.workflow import (
    Action, InvalidTransition, Policy, Role, TransitionContext, decide_route, transition,
)


def test_medium_always_routes_to_review():
    assert decide_route("medium", 1000, Policy(auto_post_cap=50000)).value == "review"


def test_high_is_blocked_from_post_even_with_auto_post_policy():
    assert decide_route("high", 100, Policy(auto_post_cap=50000)).value == "review"


def test_low_over_cap_routes_to_review():
    assert decide_route("low", 50001, Policy(auto_post_cap=50000)).value == "review"


def test_low_under_cap_can_policy_clear_only_when_maker_checker_off():
    assert decide_route("low", 500, Policy(auto_post_cap=1000, maker_checker=False)).value == "policy_clear"
    assert decide_route("low", 500, Policy(auto_post_cap=1000, maker_checker=True)).value == "review"


def test_approve_requires_approver_and_forbids_self_approval():
    ctx = TransitionContext(actor_id="u1", actor_roles={Role.APPROVER}, maker_id="u1")
    with pytest.raises(InvalidTransition, match="self-approve"):
        transition("in_review", Action.APPROVE, ctx)
    with pytest.raises(InvalidTransition, match="approver"):
        transition("in_review", Action.APPROVE,
                   TransitionContext(actor_id="u2", actor_roles={Role.AP_CLERK}, maker_id="u1"))
    assert transition("in_review", Action.APPROVE,
                      TransitionContext(actor_id="u2", actor_roles={Role.APPROVER}, maker_id="u1")) == "approved"


def test_approver_cannot_release_payment_on_same_document():
    with pytest.raises(InvalidTransition, match="separation"):
        transition("scheduled", Action.RELEASE_PAYMENT,
                   TransitionContext(actor_id="u2", actor_roles={Role.PAYER}, approved_by="u2"))


def test_reject_and_resubmit_never_post():
    ctx = TransitionContext(actor_id="u2", actor_roles={Role.APPROVER}, maker_id="u1")
    assert transition("in_review", Action.REJECT, ctx) == "rejected"
    assert transition("in_review", Action.ASK_RESUBMIT, ctx) == "resubmit"


def test_erp_failure_goes_to_exception_not_retry_transition():
    assert transition("approved", Action.ERP_FAILED,
                      TransitionContext(actor_id="agent", actor_roles=set())) == "exception"


def test_illegal_shortcut_from_extracted_to_approved_is_refused():
    with pytest.raises(InvalidTransition):
        transition("extracted", Action.APPROVE,
                   TransitionContext(actor_id="u2", actor_roles={Role.APPROVER}, maker_id="u1"))

