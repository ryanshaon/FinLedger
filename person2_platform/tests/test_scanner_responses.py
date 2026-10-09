"""Malformed scanner responses must never mark a document clean."""

from contextlib import contextmanager
import socket
import struct
import threading

import pytest

from finledger_platform.virus import ScannerError, clamd_scan

CANARY = "synthetic-invoice-canary"


@contextmanager
def replying_scanner(reply):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        port = listener.getsockname()[1]

        def serve():
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(3)
                received = b""
                while not received.endswith(struct.pack(">I", 0)):
                    chunk = connection.recv(4096)
                    if not chunk:
                        return
                    received += chunk
                connection.sendall(reply)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield port
        finally:
            thread.join(timeout=4)
            assert not thread.is_alive()


@pytest.mark.parametrize("reply", [
    b"garbageOK\0", b"stream: NOTOK\0", b"stream: OK", b"unexpected: OK\0",
    b"stream: \xffOK\0", b"stream: FOUND\0", b"stream: " + b"A" * 16384 + b" FOUND\0",
    b"stream: " + CANARY.encode() + b" ERROR\0",
], ids=["garbage", "not-ok", "unterminated", "wrong-stream", "invalid-encoding",
        "empty-signature", "oversized", "diagnostic-canary"])
def test_malformed_scanner_response_is_not_clean_and_keeps_diagnostics_safe(reply):
    with replying_scanner(reply) as port:
        with pytest.raises(ScannerError) as result:
            clamd_scan(b"synthetic bytes", "127.0.0.1", port, timeout=2)
    assert CANARY not in str(result.value)
