"""``run-once``: one job, one typed confirmation, at most one Import. SYNTHETIC Tally and platform only."""

from __future__ import annotations

import io
import json
from dataclasses import replace

import pytest

from fake_tally import FakeTally
from finledger_tally import FileJournal, Phase
from finledger_tally.__main__ import main
from finledger_tally.platform_client import ClaimedPost, PlatformClient, PlatformError, report_body
from finledger_tally.preflight import LIVE_TEST_NAME_PREFIX
from finledger_tally.run_once import ask_with_timeout, replay_unreported, run_once, run_once_problems
from test_posting import CLIENT, approval, make_job
from test_preflight import LIVE, live_settings, live_tally


class FakePlatform:
    """Hands out one claim and records what the agent says. It keeps no business logic of its own."""

    def __init__(self, job, invoice=None, company=None):
        self.job, self.results, self.released = job, [], []
        self.invoice = invoice or {"total": "1180.00", "cgst": "0", "sgst": "0", "igst": "0", "cess": "0"}
        self.company = company or {"name": LIVE[0], "guid": LIVE[1]}
        self.claims = 0
        self.fail_result = False

    def claim(self):
        if self.claims:
            return None
        self.claims += 1
        return ClaimedPost(job_id=7, payload=self.job, authorization=approval(self.job), claim_token="token-1",
                           company=self.company, invoice=self.invoice)

    def send_result(self, job_id, body):
        if self.fail_result:
            raise PlatformError("result", None)
        self.results.append(body)
        return {"document_status": "x"}

    def release(self, job_id, token, reason):
        self.released.append(reason)


@pytest.fixture
def fake():
    fakes = []

    def start(**kw):
        f = live_tally(**kw)
        fakes.append(f)
        return f
    yield start
    for f in fakes:
        f.close()


def settings(f, tmp_path, **changes):
    return replace(live_settings(f, tmp_path, posting_enabled=True, client_id=CLIENT), **changes)


def go(s, platform, answer="phrase"):
    def ask(phrase):
        return phrase if answer == "phrase" else answer
    return run_once(s, platform, disposable_guid=LIVE[1], ask=ask, say=lambda text: None)


def test_a_normal_post_sends_one_import_and_reports_it(fake, tmp_path):
    f, job = fake(), make_job()
    platform = FakePlatform(job)
    s = settings(f, tmp_path)
    out = go(s, platform)
    assert out["ok"] is True and out["outcome"] == "posted" and len(f.imports) == 1
    body = platform.results[0]
    assert body["outcome"] == "posted" and body["erp_id"] and body["claim_token"] == "token-1"
    assert body["request_sha256"] and body["response_sha256"]
    phases = [e.phase for e in FileJournal(s.journal_dir).events(job["document_id"])]
    assert phases[-1] == Phase.REPORTED.value
    assert not list((tmp_path / "state" / "claims").glob("*.json"))


def test_the_same_job_delivered_twice_sends_one_import(fake, tmp_path):
    f, job = fake(), make_job()
    s = settings(f, tmp_path)
    go(s, FakePlatform(job))
    second = FakePlatform(job)
    out = go(s, second)
    assert len(f.imports) == 1 and out["ok"] is False and out["reason"] == "preflight_failed"
    assert second.results[0]["outcome"] == "posted"


def test_nothing_to_do_is_a_clean_exit(fake, tmp_path):
    f = fake()
    platform = FakePlatform(make_job())
    platform.claims = 1
    assert go(settings(f, tmp_path), platform) == {"ok": True, "reason": "no_job"}
    assert f.imports == []


def test_a_tally_rejection_is_reported_as_failed_with_no_retry(fake, tmp_path):
    f, platform = fake(import_mode="reject"), FakePlatform(make_job())
    out = go(settings(f, tmp_path), platform)
    assert out["outcome"] == "failed" and len(f.imports) == 1
    assert platform.results[0]["error"].startswith("tally_rejected")


def test_a_lost_reply_with_the_voucher_found_afterwards_is_posted_with_no_retry(fake, tmp_path):
    f, platform = fake(import_mode="hangup_after_create"), FakePlatform(make_job())
    assert go(settings(f, tmp_path), platform)["outcome"] == "posted" and len(f.imports) == 1


def test_a_lost_reply_is_uncertain_with_no_retry(fake, tmp_path):
    f, job = fake(import_mode="hangup_before_create"), make_job()
    platform = FakePlatform(job)
    s = settings(f, tmp_path)
    out = go(s, platform)
    assert out["outcome"] == "uncertain" and len(f.imports) == 1
    assert platform.results[0]["erp_id"] is None
    f.import_mode = "create"
    again = FakePlatform(job)
    go(s, again)
    assert len(f.imports) == 1, "an uncertain send must never be sent again"
    assert again.results[0]["outcome"] in ("uncertain", "posted")


def test_a_crash_before_the_report_is_reported_on_the_next_run_without_sending(fake, tmp_path):
    f, job = fake(), make_job()
    s = settings(f, tmp_path)
    platform = FakePlatform(job)
    platform.fail_result = True
    out = go(s, platform)
    assert out["reason"] == "platform_unavailable" and len(f.imports) == 1
    assert list((tmp_path / "state" / "claims").glob("*.json")), "the claim stays so the outcome can still be reported"
    next_run = FakePlatform(job)
    assert go(s, next_run) == {"ok": False, "reason": "replayed_unreported", "count": 1}
    assert next_run.results[0]["outcome"] == "posted" and next_run.results[0]["claim_token"] == "token-1"
    assert next_run.claims == 0 and len(f.imports) == 1
    assert go(s, FakePlatform(job))["reason"] == "preflight_failed" and len(f.imports) == 1


def test_a_crash_before_any_send_releases_the_job_on_the_next_run(fake, tmp_path):
    f, job = fake(), make_job()
    s = settings(f, tmp_path)
    folder = tmp_path / "state" / "claims"
    folder.mkdir(parents=True)
    (folder / "7.json").write_text(json.dumps({"job_id": 7, "claim_token": "t", "document_id": job["document_id"],
                                               "revision": 1, "idempotency_key": job["idempotency_key"]}))
    platform = FakePlatform(job)
    assert replay_unreported(s, platform) == 1
    assert platform.released == ["not_confirmed"] and platform.results == [] and f.imports == []


def test_a_crash_mid_send_is_reported_as_uncertain(fake, tmp_path):
    f, job = fake(), make_job()
    s = settings(f, tmp_path)
    journal = FileJournal(s.journal_dir)
    journal.append(job["document_id"], Phase.SEND_STARTED, revision=1, idempotency_key=job["idempotency_key"],
                   request_sha256="a" * 64)
    folder = tmp_path / "state" / "claims"
    folder.mkdir(parents=True)
    (folder / "7.json").write_text(json.dumps({"job_id": 7, "claim_token": "t", "document_id": job["document_id"],
                                               "revision": 1, "idempotency_key": job["idempotency_key"]}))
    platform = FakePlatform(job)
    replay_unreported(s, platform)
    assert platform.results[0]["outcome"] == "uncertain" and f.imports == []


def test_a_lost_state_folder_with_the_marker_in_tally_sends_nothing(fake, tmp_path):
    f, job = fake(), make_job()
    go(settings(f, tmp_path), FakePlatform(job))
    out = go(settings(f, tmp_path, state_dir=tmp_path / "lost"), FakePlatform(job))
    assert len(f.imports) == 1 and out["ok"] is False


def test_tally_down_releases_the_job_and_sends_nothing(fake, tmp_path):
    f, platform = fake(), FakePlatform(make_job())
    f.down = True
    out = go(settings(f, tmp_path), platform)
    assert out["ok"] is False and platform.released and f.imports == [] and platform.results == []


def test_a_wrong_company_from_the_platform_sends_nothing(fake, tmp_path):
    f, platform = fake(), FakePlatform(make_job(), company={"name": LIVE[0], "guid": "0b1c2d3e-4f50-4a61-8b72-c83d94e5f601"})
    assert go(settings(f, tmp_path), platform)["reason"] == "company_mismatch"
    assert platform.released == ["company_unverified"] and f.imports == []


def test_two_loaded_companies_send_nothing(fake, tmp_path):
    other = ("Client Books Pvt Ltd", "0b1c2d3e-4f50-4a61-8b72-c83d94e5f601")
    f, platform = fake(companies=[LIVE, other]), FakePlatform(make_job())
    out = go(settings(f, tmp_path), platform)
    assert out["reason"] == "preflight_failed" and "single_company_loaded" in out["failed"] and f.imports == []


def test_an_existing_voucher_in_the_company_sends_nothing(fake, tmp_path):
    from test_isolation_check import voucher
    f, platform = fake(vouchers={LIVE[1]: [voucher(LIVE[1], 1)]}), FakePlatform(make_job())
    out = go(settings(f, tmp_path), platform)
    assert "company_has_no_vouchers" in out["failed"] and f.imports == []


@pytest.mark.parametrize("answer", ["wrong phrase", "", None])
def test_a_wrong_or_missing_phrase_releases_the_job(fake, tmp_path, answer):
    f, platform = fake(), FakePlatform(make_job())
    out = go(settings(f, tmp_path), platform, answer=answer)
    assert out["reason"] == "not_confirmed" and platform.released == ["not_confirmed"] and f.imports == []


def test_tax_on_the_invoice_is_refused_even_on_an_exempt_draft(fake, tmp_path):
    f = fake()
    platform = FakePlatform(make_job(), invoice={"total": "1180.00", "cgst": "90", "sgst": "90", "igst": "0", "cess": "0"})
    out = go(settings(f, tmp_path), platform)
    assert out["reason"] == "tax_on_invoice" and platform.results[0]["outcome"] == "failed" and f.imports == []


def test_a_total_that_differs_from_the_invoice_sends_nothing(fake, tmp_path):
    f = fake()
    platform = FakePlatform(make_job(), invoice={"total": "999.00", "cgst": "0", "sgst": "0", "igst": "0", "cess": "0"})
    out = go(settings(f, tmp_path), platform)
    assert out["ok"] is False and f.imports == []


def test_the_timeout_answer_is_none():
    assert ask_with_timeout(lambda: __import__("time").sleep(5) or "late", seconds=0.05) is None
    assert ask_with_timeout(lambda: "typed", seconds=5) == "typed"


# ---------- the command line ----------

def env_for(f, tmp_path, **changes):
    env = {"FINLEDGER_TALLY_COMPANY": LIVE[0], "FINLEDGER_TALLY_COMPANY_GUID": LIVE[1], "FINLEDGER_TALLY_CLIENT_ID": CLIENT,
           "FINLEDGER_TALLY_PORT": str(f.port), "FINLEDGER_TALLY_STATE_DIR": str(tmp_path / "state"),
           "FINLEDGER_TALLY_POSTING": "enabled", "FINLEDGER_TALLY_LIVE_TEST_COMPANY_GUID": LIVE[1],
           "FINLEDGER_PLATFORM_URL": "http://127.0.0.1:9", "FINLEDGER_AGENT_TOKEN": "secret-agent-token"}
    env.update(changes)
    return {k: v for k, v in env.items() if v is not None}


@pytest.mark.parametrize("change,problem", [
    ({"FINLEDGER_TALLY_POSTING": None}, "posting_disabled"),
    ({"FINLEDGER_TALLY_OFFLINE_XML": "enabled"}, "offline_xml_set"),
    ({"FINLEDGER_TALLY_LIVE_TEST_COMPANY_GUID": None}, "live_test_guid_mismatch"),
    ({"FINLEDGER_TALLY_COMPANY": "Client Books Pvt Ltd"}, "company_name_not_disposable"),
    ({"FINLEDGER_PLATFORM_URL": None}, "platform_not_configured"),
])
def test_run_once_exits_1_unless_every_setting_is_right(fake, tmp_path, change, problem):
    f, out = fake(), io.StringIO()
    assert main(["run-once"], env=env_for(f, tmp_path, **change), out=out) == 1
    body = json.loads(out.getvalue())
    assert problem in body["problems"] and f.imports == []
    assert "secret-agent-token" not in out.getvalue()


def test_an_unreachable_platform_is_exit_2_and_never_prints_the_token(fake, tmp_path):
    f, out = fake(), io.StringIO()
    assert main(["run-once"], env=env_for(f, tmp_path), out=out) == 2
    assert json.loads(out.getvalue())["reason"] == "platform_unavailable"
    assert "secret-agent-token" not in out.getvalue() and f.imports == []


def test_problems_are_empty_only_for_the_disposable_company(fake, tmp_path):
    f = fake()
    s = settings(f, tmp_path)
    assert run_once_problems(s, {"FINLEDGER_TALLY_LIVE_TEST_COMPANY_GUID": LIVE[1]}) == []
    assert s.company_name.startswith(LIVE_TEST_NAME_PREFIX)


def test_report_body_marks_an_uncertain_send_by_its_stable_prefix():
    job = make_job()
    claimed = ClaimedPost(job_id=7, payload=job, authorization=approval(job), claim_token="t")
    uncertain = report_body(claimed, {"ok": False, "erp_id": None,
                                      "error": "outcome_uncertain (reconciliation required; FinLedger will not resend): x"})
    assert uncertain["outcome"] == "uncertain"
    assert report_body(claimed, {"ok": False, "erp_id": None, "error": "tally_rejected: x"})["outcome"] == "failed"
    assert report_body(claimed, {"ok": True, "erp_id": "g-1", "error": None})["outcome"] == "posted"


# ---------- the journal on Windows ----------

def test_the_journal_writes_exact_bytes_and_reads_its_own_lines(tmp_path):
    journal = FileJournal(tmp_path)
    document = make_job()["document_id"]
    journal.append(document, Phase.PREVIEW_READY, revision=1, idempotency_key=f"{document}:r1")
    journal.append(document, Phase.REPORTED, revision=1, idempotency_key=f"{document}:r1")
    raw = (tmp_path / document / "journal.jsonl").read_bytes()
    assert b"\r\n" not in raw and raw.count(b"\n") == 2
    assert [e.phase for e in journal.events(document)] == ["preview_ready", "reported"]
    path, _ = journal.save_bytes(document, "0001_request.xml", b"<a>\n<b/>\n</a>\n")
    assert path.read_bytes() == b"<a>\n<b/>\n</a>\n"


def test_the_client_keeps_the_token_out_of_error_text():
    client = PlatformClient("http://127.0.0.1:9", "secret-agent-token", client_id=CLIENT, timeout=0.2)
    with pytest.raises(PlatformError) as caught:
        client.claim()
    assert "secret-agent-token" not in str(caught.value)
    assert FakeTally  # the fake in this module is the only Tally these tests reach
