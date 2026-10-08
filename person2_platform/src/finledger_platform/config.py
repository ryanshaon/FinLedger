"""Settings from environment. One place, read once."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"missing required env var {name}")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("FINLEDGER_DATABASE_URL"))  # app role, RLS applies
    owner_database_url: str = field(default_factory=lambda: _env("FINLEDGER_OWNER_DATABASE_URL", ""))  # migrations
    app_base_url: str = field(default_factory=lambda: _env("FINLEDGER_APP_BASE_URL", "http://localhost:8000"))
    signing_secret: bytes = field(default_factory=lambda: _env("FINLEDGER_SIGNING_SECRET").encode())
    enc_key: str = field(default_factory=lambda: _env("FINLEDGER_ENC_KEY", ""))  # Fernet key for ERP secrets

    store: str = field(default_factory=lambda: _env("FINLEDGER_STORE", "local"))  # local | s3
    store_root: str = field(default_factory=lambda: _env("FINLEDGER_STORE_ROOT", "./var/objects"))
    s3_bucket: str = field(default_factory=lambda: _env("FINLEDGER_S3_BUCKET", ""))
    s3_endpoint_url: str = field(default_factory=lambda: _env("FINLEDGER_S3_ENDPOINT_URL", ""))
    s3_region: str = field(default_factory=lambda: _env("FINLEDGER_S3_REGION", ""))
    s3_addressing_style: str = field(default_factory=lambda: _env("FINLEDGER_S3_ADDRESSING_STYLE", ""))
    s3_server_side_encryption: str = field(default_factory=lambda: _env("FINLEDGER_S3_SERVER_SIDE_ENCRYPTION", "auto"))

    inbound_domain: str = field(default_factory=lambda: _env("FINLEDGER_INBOUND_DOMAIN", "inbound.finledger.in"))
    inbound_webhook_secret: bytes = field(default_factory=lambda: _env("FINLEDGER_INBOUND_WEBHOOK_SECRET").encode())
    inbound_authserv_id: str = field(default_factory=lambda: _env("FINLEDGER_INBOUND_AUTHSERV_ID", ""))

    virus_scanner: str = field(default_factory=lambda: _env("FINLEDGER_VIRUS_SCANNER", "clamd"))  # clamd | eicar
    clamd_host: str = field(default_factory=lambda: _env("FINLEDGER_CLAMD_HOST", "127.0.0.1"))
    clamd_port: int = field(default_factory=lambda: int(_env("FINLEDGER_CLAMD_PORT", "3310")))

    max_upload_bytes: int = field(default_factory=lambda: int(_env("FINLEDGER_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))))
    rate_per_token_hour: int = field(default_factory=lambda: int(_env("FINLEDGER_RATE_PER_TOKEN_HOUR", "120")))
    rate_per_ip_hour: int = field(default_factory=lambda: int(_env("FINLEDGER_RATE_PER_IP_HOUR", "60")))
    render_page_cap: int = field(default_factory=lambda: int(_env("FINLEDGER_RENDER_PAGE_CAP", "30")))

    def inbound_address(self, slug: str) -> str:
        return f"invoices.{slug}@{self.inbound_domain}"

    def upload_link(self, public_token: str) -> str:
        return f"{self.app_base_url.rstrip('/')}/i/{public_token}"
