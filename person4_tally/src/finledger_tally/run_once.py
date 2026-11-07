"""Phase 1's only way to post: one job, in one console, with a typed confirmation. No loop, no service, no retry.

Order: replay any outcome not yet reported -> claim one job -> check the company and that the invoice carries no tax ->
preflight -> show the summary and ask for the phrase -> one Import -> report -> record that it was reported.

A claim is written to the state folder before anything else happens, so a crash at any later point can still report
what the journal says. A job is released only when the journal shows nothing was sent.
"""

from __future__ import annotations

import json
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import AgentSettings
from .journal import FileJournal, Phase, gate, private_dir
from .platform_client import ClaimedPost, PlatformClient, PlatformError, report_body
from .posting import State, _UNCERTAIN_PREFIX
from .preflight import LIVE_TEST_NAME_PREFIX, execute_live_import, live_import_preflight

CONFIRM_SECONDS = 120.0
_RELEASE_REASONS = {State.TALLY_UNAVAILABLE: "tally_unavailable", State.SEND_NOT_DELIVERED: "send_not_delivered"}


def run_once_problems(settings: AgentSettings, env: Mapping[str, str]) -> list[str]:
    """Why this process must not post at all. Empty means it may try; the preflight then checks the company."""
    problems = []
    if not settings.posting_enabled:
        problems.append("posting_disabled")
    if env.get("FINLEDGER_TALLY_OFFLINE_XML") or settings.offline_xml_enabled:
        problems.append("offline_xml_set")
    if not settings.company_guid or env.get("FINLEDGER_TALLY_LIVE_TEST_COMPANY_GUID") != settings.company_guid:
        problems.append("live_test_guid_mismatch")
    if not str(settings.company_name or "").startswith(LIVE_TEST_NAME_PREFIX):
        problems.append("company_name_not_disposable")
    return problems


def ask_with_timeout(prompt: Callable[[], str], seconds: float = CONFIRM_SECONDS) -> str | None:
    """The typed answer, or None after ``seconds``. The reader is a daemon thread, so a timeout cannot hold the process."""
    answer: list[str] = []

    def read() -> None:
        try:
            answer.append(prompt())
        except (EOFError, OSError, ValueError):
            pass
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(seconds)
    return answer[0] if answer else None


def _no_tax(invoice: Mapping[str, Any] | None) -> bool:
    try:
        return all(Decimal(str((invoice or {})[field])) == 0 for field in ("cgst", "sgst", "igst", "cess"))
    except (KeyError, InvalidOperation, ValueError):
        return False


def _claim_path(settings: AgentSettings, job_id: Any) -> Path:
    return Path(settings.state_dir) / "claims" / f"{int(job_id)}.json"


def _save_claim(settings: AgentSettings, claimed: ClaimedPost) -> Path:
    path = _claim_path(settings, claimed.job_id)
    private_dir(path.parent)
    path.write_text(json.dumps({"job_id": claimed.job_id, "claim_token": claimed.claim_token,
                                "document_id": claimed.payload["document_id"], "revision": claimed.payload["revision"],
                                "idempotency_key": claimed.payload["idempotency_key"]}), encoding="utf-8")
    return path


def _journal_outcome(events: list[Any]) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """(outcome, erp_id, error, request_sha256, response_sha256) from the journal alone; outcome None means no send."""
    status = gate(events)
    request = next((e.request_sha256 for e in reversed(events) if e.request_sha256), None)
    response = next((e.response_sha256 for e in reversed(events) if e.response_sha256), None)
    if status.status == "posted":
        return "posted", status.posted.erp_id, None, request, response
    if status.status == "blocked":
        return "uncertain", None, f"{_UNCERTAIN_PREFIX}: reported from the agent's journal", request, response
    if status.status == "rejected":
        return "failed", None, f"tally_rejected: {status.rejected.detail}"[:2000], request, response
    return None, None, None, request, response


def _record_reported(journal: FileJournal, document_id: str, revision: int, key: str) -> None:
    with journal.lock(document_id):
        journal.append(document_id, Phase.REPORTED, revision=revision, idempotency_key=key)


def replay_unreported(settings: AgentSettings, client: PlatformClient) -> int:
    """Report what the journal knows for any claim a crash left behind. Sends nothing to Tally. Returns how many."""
    folder = Path(settings.state_dir) / "claims"
    journal = FileJournal(settings.journal_dir)
    count = 0
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        saved = json.loads(path.read_text(encoding="utf-8"))
        events = journal.events(saved["document_id"])
        if not any(e.phase == Phase.REPORTED.value and e.revision == saved["revision"] for e in events):
            outcome, erp_id, error, request, response = _journal_outcome(events)
            if outcome is None:
                try:
                    client.release(saved["job_id"], saved["claim_token"], "not_confirmed")
                except PlatformError as exc:
                    if exc.status != 409:  # 409: the lease expired, and the job is waiting for a person
                        raise
            else:
                client.send_result(saved["job_id"], {
                    "claim_token": saved["claim_token"], "revision": saved["revision"],
                    "idempotency_key": saved["idempotency_key"], "outcome": outcome, "erp_id": erp_id, "error": error,
                    "request_sha256": request, "response_sha256": response})
                _record_reported(journal, saved["document_id"], saved["revision"], saved["idempotency_key"])
        path.unlink()
        count += 1
    return count


def run_once(settings: AgentSettings, client: PlatformClient, *, disposable_guid: str,
             ask: Callable[[str], str | None], say: Callable[[str], None]) -> dict[str, Any]:
    """One job at most. Returns ``{"ok", "reason", ...}``; never raises for a Tally or platform fault."""
    try:
        replayed = replay_unreported(settings, client)
    except PlatformError as exc:
        return {"ok": False, "reason": "platform_unavailable", "error": str(exc)}
    if replayed:
        return {"ok": False, "reason": "replayed_unreported", "count": replayed}
    try:
        claimed = client.claim()
    except PlatformError as exc:
        return {"ok": False, "reason": "platform_unavailable", "error": str(exc)}
    if claimed is None:
        return {"ok": True, "reason": "no_job"}
    claim_file = _save_claim(settings, claimed)

    def release(reason: str, why: str) -> dict[str, Any]:
        client.release(claimed.job_id, claimed.claim_token, reason)
        claim_file.unlink()
        return {"ok": False, "reason": why, "job_id": claimed.job_id}

    def report(result: Mapping[str, Any], *, request: str | None, response: str | None, reason: str, ok: bool):
        body = report_body(claimed, result, request_sha256=request, response_sha256=response)
        client.send_result(claimed.job_id, body)
        _record_reported(FileJournal(settings.journal_dir), claimed.payload["document_id"],
                         claimed.payload["revision"], claimed.payload["idempotency_key"])
        claim_file.unlink()
        return {"ok": ok, "reason": reason, "outcome": body["outcome"], "erp_id": body["erp_id"], "job_id": claimed.job_id}

    try:
        company = claimed.company or {}
        if company.get("guid") != settings.company_guid or company.get("name") != settings.company_name:
            return release("company_unverified", "company_mismatch")
        if not _no_tax(claimed.invoice):
            return report({"ok": False, "erp_id": None, "error": "validation_rejected: the invoice carries tax"},
                          request=None, response=None, reason="tax_on_invoice", ok=False)
        expected_total = Decimal(str((claimed.invoice or {})["total"]))
        preflight = live_import_preflight(claimed.payload, claimed.authorization, settings,
                                          disposable_company_guid=disposable_guid, posting_expected=True,
                                          expected_total=expected_total)
        if not preflight.ready:
            failed = [c.name for c in preflight.failed]
            outcome, erp_id, error, request, response = _journal_outcome(
                FileJournal(settings.journal_dir).events(claimed.payload["document_id"]))
            if outcome is None:
                result = release("company_unverified", "preflight_failed")
            else:
                result = report({"ok": outcome == "posted", "erp_id": erp_id, "error": error}, request=request,
                                response=response, reason="preflight_failed", ok=False)
            return {**result, "failed": failed}
        say(preflight.summary())
        typed = ask(preflight.confirmation or "")
        if typed is None or typed.strip() != preflight.confirmation:
            return release("not_confirmed", "not_confirmed")
        final, outcome = execute_live_import(claimed.payload, claimed.authorization, settings,
                                             disposable_company_guid=disposable_guid, confirmation=preflight.confirmation,
                                             expected_total=expected_total)
        if outcome is None:
            return {**release("company_unverified", "preflight_failed"), "failed": [c.name for c in final.failed]}
        result = outcome.erp_result()
        if result is None:
            if outcome.state in _RELEASE_REASONS:
                return release(_RELEASE_REASONS[outcome.state], outcome.state)
            return release("company_unverified", outcome.state)
        return report(result, request=outcome.request_sha256, response=outcome.response_sha256, reason=outcome.state,
                      ok=bool(result["ok"]))
    except PlatformError as exc:  # the claim file stays: the next run reports what the journal says
        return {"ok": False, "reason": "platform_unavailable", "error": str(exc), "job_id": claimed.job_id}


__all__ = ["CONFIRM_SECONDS", "ask_with_timeout", "replay_unreported", "run_once", "run_once_problems"]
