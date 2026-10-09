"""Virus scanning. ClamAV's clamd over TCP (INSTREAM). Fails closed: a scanner error is a retry, never a pass."""
from __future__ import annotations

import socket
import struct

from .config import Settings

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!H+H*"


class ScannerError(RuntimeError):
    """Scanner unreachable or confused. Transient: the ingest job retries, the file stays unscanned."""


def clamd_scan(data: bytes, host: str, port: int, timeout: float = 30.0) -> str | None:
    """Return the signature name if infected, None if clean."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), 64 * 1024):
                chunk = data[i:i + 64 * 1024]
                s.sendall(struct.pack(">I", len(chunk)) + chunk)
            s.sendall(struct.pack(">I", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                buf = s.recv(4096)
                if not buf:
                    break
                reply += buf
                if len(reply) > 4096:
                    raise ScannerError("invalid clamd response")
    except OSError as e:
        raise ScannerError(f"clamd unreachable at {host}:{port}: {e}") from e
    if reply == b"stream: OK\0":
        return None
    if not reply.endswith(b"\0"):
        raise ScannerError("invalid clamd response")
    try:
        text = reply[:-1].decode("utf-8")
    except UnicodeDecodeError:
        raise ScannerError("invalid clamd response") from None
    if text.startswith("stream: ") and text.endswith(" FOUND"):
        signature = text[len("stream: "):-len(" FOUND")]
        if (signature and signature == signature.strip()
                and all(ord(c) >= 32 and ord(c) != 127 for c in signature)):
            return signature
    # Never reflect an untrusted scanner reply (possibly document data) in diagnostics.
    raise ScannerError("invalid clamd response")


def eicar_scan(data: bytes) -> str | None:
    # ponytail: dev/test only, catches the EICAR test string and nothing else. Production sets clamd.
    return "Eicar-Test-Signature" if EICAR in data else None


def scan(data: bytes, settings: Settings) -> str | None:
    if settings.virus_scanner == "eicar":
        return eicar_scan(data)
    return clamd_scan(data, settings.clamd_host, settings.clamd_port)
