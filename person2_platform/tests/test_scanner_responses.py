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
        failures = []
        request_complete = threading.Event()
        reply_sent = threading.Event()

        def serve():
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(3)

                    def read_exact(size):
                        received = bytearray()
                        while len(received) < size:
                            chunk = connection.recv(size - len(received))
                            assert chunk, "incomplete INSTREAM request"
                            received.extend(chunk)
                        return bytes(received)

                    assert read_exact(10) == b"zINSTREAM\0", "invalid INSTREAM command"
                    while True:
                        size = struct.unpack(">I", read_exact(4))[0]
                        if size == 0:
                            break
                        read_exact(size)
                    request_complete.set()
                    connection.sendall(reply)
                    reply_sent.set()
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield port
        finally:
            thread.join(timeout=4)
            assert not thread.is_alive(), "scanner server did not finish"
            if failures:
                raise AssertionError("scanner server failed") from failures[0]
            assert request_complete.is_set(), "scanner server did not receive complete INSTREAM"
            assert reply_sent.is_set(), "scanner server did not send reply"


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


def test_clean_response_via_same_scanner_harness():
    # Internal zero bytes must not be mistaken for the zero-length frame.
    with replying_scanner(b"stream: OK\0") as port:
        assert clamd_scan(b"synthetic\0\0\0\0bytes", "127.0.0.1", port, timeout=2) is None


@pytest.mark.parametrize("failure", ["accept", "incomplete-request", "send-reply"])
def test_harness_rejects_connection_failure_without_reply_delivery(monkeypatch, failure):
    """A blocked connection must not masquerade as malformed-reply coverage."""
    class BrokenConnection:
        def __init__(self):
            self.chunks = iter([b"zINSTREAM\0", b"" if failure == "incomplete-request" else b"\0" * 4])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def recv(self, size):
            return next(self.chunks)

        def sendall(self, reply):
            raise OSError("synthetic reply delivery failure")

    class UnreachableListener:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, address):
            pass

        def listen(self, backlog):
            pass

        def settimeout(self, timeout):
            pass

        def getsockname(self):
            return ("127.0.0.1", 12345)

        def accept(self):
            if failure == "accept":
                raise socket.timeout("synthetic accept failure")
            return BrokenConnection(), ("127.0.0.1", 12346)

    def blocked_connection(*args, **kwargs):
        raise OSError("synthetic blocked connection")

    monkeypatch.setattr(socket, "socket", UnreachableListener)
    monkeypatch.setattr(socket, "create_connection", blocked_connection)
    with pytest.raises(AssertionError, match="scanner server failed"):
        with replying_scanner(b"stream: OK\0") as port:
            with pytest.raises(ScannerError):
                clamd_scan(b"synthetic bytes", "127.0.0.1", port, timeout=2)
