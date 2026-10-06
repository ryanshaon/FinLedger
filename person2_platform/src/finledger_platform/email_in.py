"""Inbound email door: invoices.{slug}@{inbound_domain}.

The MX provider (SES / Mailgun / Postmark / Postfix pipe) POSTs the raw RFC 822 message to /inbound/email,
signed with HMAC-SHA256 over the body. SPF/DKIM/DMARC are read from the Authentication-Results header our own
MTA stamped (matched by authserv-id), never from headers the sender could have written.
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

_SLUG_ADDR = re.compile(r"^invoices\.([a-z0-9][a-z0-9-]{1,38}[a-z0-9])@(.+)$")
_RESULT = re.compile(r"\b(spf|dkim|dmarc)\s*=\s*([a-z]+)", re.I)
_DKIM_D = re.compile(r"\bheader\.d\s*=\s*([^\s;]+)", re.I)
MIN_INLINE_IMAGE_BYTES = 10 * 1024  # smaller inline images are signature logos, not bills


def slug_from_address(addr: str, inbound_domain: str) -> str | None:
    m = _SLUG_ADDR.match(parseaddr(addr)[1].strip().lower())
    return m.group(1) if m and m.group(2) == inbound_domain.lower() else None


def _domain(addr: str) -> str:
    return parseaddr(addr or "")[1].rpartition("@")[2].lower()


def auth_results(msg: EmailMessage, authserv_id: str) -> dict[str, str]:
    """spf/dkim/dmarc verdicts from the trusted Authentication-Results header (topmost one if no id is set)."""
    for header in msg.get_all("Authentication-Results", []):
        header = str(header)
        server = header.split(";", 1)[0].strip().split()[0].lower() if header.strip() else ""
        if authserv_id and server != authserv_id.lower():
            continue
        out = {k.lower(): v.lower() for k, v in _RESULT.findall(header)}
        if d := _DKIM_D.search(header):
            out["dkim_domain"] = d.group(1).lower()
        return out
    return {}


@dataclass
class ParsedEmail:
    recipients: list[str]
    sender: str
    sender_domain: str
    subject: str
    message_id: str
    auth: dict[str, str]
    phish_flag: bool
    phish_reasons: list[str]
    attachments: list[tuple[str, bytes]] = field(default_factory=list)

    @property
    def intake_key(self) -> str:
        return "email:" + hashlib.sha256(self.message_id.encode()).hexdigest()[:32]


def parse(raw: bytes, authserv_id: str = "") -> ParsedEmail:
    msg: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)  # type: ignore[assignment]
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    sender_domain = _domain(sender)
    auth = auth_results(msg, authserv_id)

    reasons = []
    if auth.get("dmarc") == "fail":
        reasons.append("dmarc_fail")
    if auth.get("spf") != "pass" and auth.get("dkim") != "pass":
        reasons.append("spf_dkim_not_pass")
    if (dkim_d := auth.get("dkim_domain")) and auth.get("dkim") == "pass" and not (
            sender_domain == dkim_d or sender_domain.endswith("." + dkim_d)):
        reasons.append("dkim_domain_not_aligned")
    reply_to = _domain(str(msg.get("Reply-To", "")))
    if reply_to and reply_to != sender_domain:
        reasons.append("reply_to_domain_differs")

    attachments: list[tuple[str, bytes]] = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_type() == "message/rfc822":
            continue
        filename = part.get_filename() or ""
        disposition = part.get_content_disposition()
        if part.get_content_maintype() == "text" and not filename:
            continue  # the body
        payload = part.get_payload(decode=True) or b""
        if (disposition == "inline" and part.get("Content-ID") and part.get_content_maintype() == "image"
                and len(payload) < MIN_INLINE_IMAGE_BYTES):
            continue  # logo in the signature
        if disposition == "attachment" or filename or part.get_content_maintype() in ("application", "image"):
            attachments.append((filename or f"attachment-{len(attachments) + 1}", payload))

    recipients = [a for _, a in getaddresses([str(v) for h in ("Delivered-To", "X-Original-To", "To", "Cc")
                                               for v in msg.get_all(h, [])])]
    message_id = str(msg.get("Message-ID", "")).strip() or hashlib.sha256(raw).hexdigest()
    return ParsedEmail(recipients, sender, sender_domain, str(msg.get("Subject", ""))[:300], message_id, auth,
                       bool(reasons), reasons, attachments)
