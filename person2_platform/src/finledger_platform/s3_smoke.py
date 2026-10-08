"""Storage smoke test for the configured S3-compatible bucket (e.g. Supabase Storage). Leaves nothing behind.

Puts one synthetic object under a well-formed tenant key, reads it back, fetches it through a presigned URL,
confirms an unsigned public URL is refused (bucket really is private), then deletes it.

    FINLEDGER_STORE=s3 finledger-platform s3-smoke
"""
from __future__ import annotations

import secrets
import urllib.error
import urllib.request
from datetime import datetime, timezone
from uuid import uuid4

from .isolation_check import Report
from .store import S3Store, object_key


def _http_get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def run(store: S3Store, endpoint_url: str = "") -> Report:
    rep = Report()
    key = object_key(uuid4(), uuid4(), datetime.now(timezone.utc), "smoke.txt")
    body = b"finledger smoke " + secrets.token_hex(8).encode()
    try:
        store.put(key, body, "text/plain")
        rep.check(True, "put object")
        rep.check(store.get(key) == body, "get object returns identical bytes")
        status, data = _http_get(store.signed_url(key, ttl=60, filename="smoke.txt"))
        rep.check(status == 200 and data == body, "presigned GET returns the object")
        if endpoint_url:
            # Supabase serves public buckets at /storage/v1/object/public/<bucket>/<key>; a private one must refuse.
            base = endpoint_url.rstrip("/").removesuffix("/s3")
            status, _ = _http_get(f"{base}/object/public/{store.bucket}/{key}")
            rep.check(status >= 400, f"unsigned public URL refused (HTTP {status})")
    finally:
        try:
            store.s3.delete_object(Bucket=store.bucket, Key=key)
            rep.check(True, "deleted smoke object")
        except Exception as exc:  # report, never mask the original failure
            rep.check(False, f"delete smoke object {key}: {type(exc).__name__}")
    return rep
