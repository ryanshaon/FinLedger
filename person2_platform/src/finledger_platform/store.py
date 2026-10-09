"""Per-client object store. Every key is client_id/yyyy/mm/doc_id/<name>. No other shape is accepted."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID

from .config import Settings

_KEY_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/\d{4}/\d{2}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[A-Za-z0-9._/-]+$"
)


def object_key(client_id: UUID | str, document_id: UUID | str, received_at: datetime, name: str) -> str:
    key = f"{client_id}/{received_at:%Y}/{received_at:%m}/{document_id}/{name}"
    check_key(key)
    return key


def check_key(key: str) -> None:
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key) or ".." in key:
        raise ValueError(f"object key must be client_id/yyyy/mm/doc_id/name, got {key!r}")


class LocalStore:
    """Filesystem store for dev and single-box installs. Signed URLs are HMAC tokens served by /files/."""

    def __init__(self, root: str, secret: bytes, base_url: str):
        self.root = Path(root).resolve()
        if os.name == "nt" and not str(self.root).startswith("\\\\?\\"):
            self.root = Path("\\\\?\\" + str(self.root))  # keys are ~110 chars; lift Windows' 260-char MAX_PATH
        self.secret = secret
        self.base_url = base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        check_key(key)
        return self.root / key

    def put(self, key: str, data: bytes, mime: str) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def signed_url(self, key: str, ttl: int = 300, filename: str = "") -> str:
        """Issue a local URL with a positive integer TTL in seconds (no duration cap)."""
        check_key(key)
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            raise ValueError("local signed URL TTL must be a positive integer in seconds")
        body = base64.urlsafe_b64encode(json.dumps({"k": key, "e": int(time.time()) + ttl, "f": filename}).encode()).decode()
        sig = hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{self.base_url}/files/{body}.{sig}"

    def verify_token(self, token: str) -> tuple[str, str] | None:
        """Return (key, filename) for a valid, unexpired token, else None."""
        if not isinstance(token, str):
            return None
        body, _, sig = token.rpartition(".")
        if not body or not re.fullmatch(r"[0-9a-f]{64}", sig):
            return None
        try:
            encoded = body.encode("ascii")
            good = hmac.new(self.secret, encoded, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, good):
                return None
            claims = json.loads(base64.b64decode(encoded, altchars=b"-_", validate=True))
            if not isinstance(claims, dict):
                return None
            expiry = claims.get("e")
            # Python integers are finite; avoid float conversion of large valid integers.
            if (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                    or (isinstance(expiry, float) and not math.isfinite(expiry))
                    or expiry <= time.time()):
                return None
            key, filename = claims.get("k"), claims.get("f", "")
            check_key(key)
            if not isinstance(filename, str):
                return None
            filename.encode("utf-8")  # reject lone surrogates before header construction
        except (ValueError, RecursionError):
            return None
        return key, filename


class S3Store:
    """Private S3-compatible bucket; review UI gets presigned GETs."""

    def __init__(self, bucket: str, client=None, *, endpoint_url: str = "", region: str = "",
                 addressing_style: str = "", server_side_encryption: str = "auto"):
        import boto3  # optional dependency: pip install finledger-platform[s3]
        from botocore.config import Config

        if addressing_style not in ("", "auto", "path", "virtual"):
            raise ValueError("S3 addressing style must be auto, path, or virtual")

        self.bucket = bucket
        self.server_side_encryption = (
            "AES256" if not endpoint_url else None
        ) if server_side_encryption == "auto" else server_side_encryption or None
        options = {}
        if endpoint_url:
            options["endpoint_url"] = endpoint_url
        if region:
            options["region_name"] = region
        style = addressing_style or ("path" if endpoint_url else "")
        if style or endpoint_url:
            options["config"] = Config(signature_version="s3v4", s3={"addressing_style": style})
        self.s3 = client if client is not None else boto3.client("s3", **options)

    def put(self, key: str, data: bytes, mime: str) -> None:
        check_key(key)
        params = {"Bucket": self.bucket, "Key": key, "Body": data, "ContentType": mime}
        if self.server_side_encryption:
            params["ServerSideEncryption"] = self.server_side_encryption
        self.s3.put_object(**params)

    def get(self, key: str) -> bytes:
        check_key(key)
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def signed_url(self, key: str, ttl: int = 300, filename: str = "") -> str:
        check_key(key)
        params = {"Bucket": self.bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'inline; filename="{filename}"'
        return self.s3.generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)


def make_store(settings: Settings) -> LocalStore | S3Store:
    if settings.store == "s3":
        return S3Store(settings.s3_bucket, endpoint_url=settings.s3_endpoint_url,
                       region=settings.s3_region, addressing_style=settings.s3_addressing_style,
                       server_side_encryption=settings.s3_server_side_encryption)
    return LocalStore(settings.store_root, settings.signing_secret, settings.app_base_url)
