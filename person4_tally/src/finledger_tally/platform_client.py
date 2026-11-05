"""The agent's HTTP client for person2's post-job routes: claim, result, release. Implements ``JobSource``/``ResultSink``.

The bearer token is sent only to the configured platform, is never printed, and never follows a redirect. Error text
carries a route name and a status code only: a reply body is not read, because it could echo what was sent.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from .agent import ClaimedJob
from .posting import _UNCERTAIN_PREFIX, PostAuthorization


class PlatformError(RuntimeError):
    def __init__(self, route: str, status: int | None):
        super().__init__(f"{route}: {'unreachable' if status is None else f'HTTP {status}'}")
        self.route, self.status = route, status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # the bearer token must never follow a redirect to another host
        return None


@dataclass(frozen=True)
class ClaimedPost(ClaimedJob):
    claim_token: str = ""
    company: Mapping[str, Any] | None = None
    invoice: Mapping[str, Any] | None = None


def report_body(claimed: ClaimedJob, result: Mapping[str, Any], *, request_sha256: str | None = None,
                response_sha256: str | None = None) -> dict[str, Any]:
    """``PostOutcome.erp_result()`` as the result route's body. A stable prefix marks an uncertain send."""
    error = result.get("error")
    outcome = "posted" if result.get("ok") else "uncertain" if str(error or "").startswith(_UNCERTAIN_PREFIX) else "failed"
    return {"claim_token": getattr(claimed, "claim_token", ""), "revision": claimed.payload["revision"],
            "idempotency_key": claimed.payload["idempotency_key"], "outcome": outcome, "erp_id": result.get("erp_id"),
            "error": error, "request_sha256": request_sha256, "response_sha256": response_sha256}


class PlatformClient:
    def __init__(self, url: str, token: str, *, client_id: str, timeout: float = 30.0, lease_seconds: int = 300):
        self._url, self._token, self._client_id = url.rstrip("/"), token, client_id
        self._timeout, self._lease = timeout, lease_seconds

    def _post(self, route: str, body: Mapping[str, Any]) -> tuple[int, Any]:
        request = urllib.request.Request(self._url + route, data=json.dumps(body).encode("utf-8"), method="POST",
                                         headers={"Authorization": f"Bearer {self._token}",
                                                  "Content-Type": "application/json"})
        try:
            with urllib.request.build_opener(_NoRedirect).open(request, timeout=self._timeout) as response:
                data = response.read()
                return response.status, json.loads(data) if data else None
        except urllib.error.HTTPError as exc:
            raise PlatformError(route.rsplit("/", 1)[-1], exc.code) from None
        except (OSError, ValueError):
            raise PlatformError(route.rsplit("/", 1)[-1], None) from None

    def claim(self) -> ClaimedPost | None:
        status, body = self._post("/agent/jobs/claim", {"lease_seconds": self._lease})
        if status == 204 or body is None:
            return None
        auth = PostAuthorization.from_rows(self._client_id, body["document"], body["attempt"], body["approvals"])
        return ClaimedPost(job_id=body["job_id"], payload=body["payload"], authorization=auth,
                           claim_token=str(body["claim_token"]), company=body["company"], invoice=body["invoice"])

    def send_result(self, job_id: Any, body: Mapping[str, Any]) -> Any:
        return self._post(f"/agent/jobs/{int(job_id)}/result", body)[1]

    def report(self, claimed: ClaimedJob, result: Mapping[str, Any]) -> None:
        self.send_result(claimed.job_id, report_body(claimed, result))

    def release(self, job_id: Any, claim_token: str, reason: str) -> None:
        self._post(f"/agent/jobs/{int(job_id)}/release", {"claim_token": claim_token, "reason": reason})


__all__ = ["ClaimedPost", "PlatformClient", "PlatformError", "report_body"]
