"""Pure workflow policy: only this module decides whether a document may move."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InvalidTransition(ValueError):
    pass


class Role(str, Enum):
    AP_CLERK = "ap_clerk"
    APPROVER = "approver"
    PAYER = "payer"
    FIRM_ADMIN = "firm_admin"


class Action(str, Enum):
    EXTRACTED = "extracted"
    SCORED = "scored"
    VENDOR_RESOLVED = "vendor_resolved"
    MATCHED = "matched"
    CODED = "coded"
    TAX_CHECKED = "tax_checked"
    SEND_REVIEW = "send_review"
    POLICY_CLEAR = "policy_clear"
    APPROVE = "approve"
    APPROVE_DRAFT = "approve_draft"
    SCHEDULE = "schedule"
    RELEASE_PAYMENT = "release_payment"
    POSTED = "posted"
    RECONCILED = "reconciled"
    CLOSE = "close"
    REJECT = "reject"
    ASK_RESUBMIT = "ask_resubmit"
    ERP_FAILED = "erp_failed"


class Route(str, Enum):
    REVIEW = "review"
    POLICY_CLEAR = "policy_clear"


@dataclass(frozen=True)
class Policy:
    auto_post_cap: float = 0
    maker_checker: bool = True


@dataclass(frozen=True)
class TransitionContext:
    actor_id: str
    actor_roles: set[Role]
    maker_id: str | None = None
    approved_by: str | None = None


def decide_route(band: str, total: float, policy: Policy) -> Route:
    if band != "low" or policy.maker_checker or policy.auto_post_cap <= 0 or total > policy.auto_post_cap:
        return Route.REVIEW
    return Route.POLICY_CLEAR


_SIMPLE = {
    ("received", Action.EXTRACTED): "extracted", ("extracted", Action.SCORED): "scored",
    ("scored", Action.VENDOR_RESOLVED): "vendor_resolved", ("vendor_resolved", Action.MATCHED): "matched",
    ("matched", Action.CODED): "coded", ("coded", Action.TAX_CHECKED): "tax_checked",
    ("tax_checked", Action.SEND_REVIEW): "in_review", ("tax_checked", Action.POLICY_CLEAR): "approved",
    ("approved", Action.SCHEDULE): "scheduled", ("approved", Action.POSTED): "posted",
    ("scheduled", Action.POSTED): "posted", ("posted", Action.RECONCILED): "reconciled",
    ("reconciled", Action.CLOSE): "closed",
}


def transition(current: str, action: Action, ctx: TransitionContext) -> str:
    if action in {Action.REJECT, Action.ASK_RESUBMIT}:
        if current != "in_review":
            raise InvalidTransition(f"cannot {action.value} from {current}")
        if not ({Role.APPROVER, Role.FIRM_ADMIN} & ctx.actor_roles):
            raise InvalidTransition("approver role required")
        return "rejected" if action == Action.REJECT else "resubmit"
    if action in {Action.APPROVE, Action.APPROVE_DRAFT}:
        if current != "in_review":
            raise InvalidTransition(f"cannot approve from {current}")
        if ctx.actor_id == ctx.maker_id:
            raise InvalidTransition("self-approve is forbidden")
        if not ({Role.APPROVER, Role.FIRM_ADMIN} & ctx.actor_roles):
            raise InvalidTransition("approver role required")
        return "approved"
    if action == Action.RELEASE_PAYMENT:
        if current != "scheduled" or Role.PAYER not in ctx.actor_roles:
            raise InvalidTransition("payer role required from scheduled")
        if ctx.actor_id == ctx.approved_by:
            raise InvalidTransition("separation of duties: approver cannot release payment")
        return "paid"
    if action == Action.ERP_FAILED:
        if current not in {"approved", "scheduled", "paid"}:
            raise InvalidTransition(f"ERP failure invalid from {current}")
        return "exception"
    try:
        return _SIMPLE[(current, action)]
    except KeyError as exc:
        raise InvalidTransition(f"cannot {action.value} from {current}") from exc

