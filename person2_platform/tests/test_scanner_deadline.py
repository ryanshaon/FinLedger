"""Aggregate scanner deadlines with deterministic time and no real sockets."""
from __future__ import annotations

import socket

import pytest

from finledger_platform import virus


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeSocket:
    def __init__(self, clock, sends, replies, honor_timeout):
        self.clock = clock
        self.sends = iter(sends)
        self.replies = iter(replies)
        self.honor_timeout = honor_timeout
        self.timeout = None
        self.timeouts = []
        self.sent = []
        self.receives = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def settimeout(self, timeout):
        self.timeout = timeout
        self.timeouts.append(timeout)

    def elapse(self, duration):
        if self.honor_timeout and duration > self.timeout:
            self.clock.now += self.timeout
            raise socket.timeout("timed out")
        self.clock.now += duration

    def sendall(self, data):
        self.sent.append(data)
        self.elapse(next(self.sends, 0))

    def recv(self, size):
        assert size == 4096
        self.receives += 1
        duration, reply = next(self.replies)
        self.elapse(duration)
        return reply


@pytest.fixture
def scanner(monkeypatch):
    def install(*, connect=0, sends=(), replies=((0, b"stream: OK\0"),), honor_timeout=True):
        clock = Clock()
        sock = FakeSocket(clock, sends, replies, honor_timeout)
        connections = []

        def create_connection(address, timeout):
            assert address == ("scanner.example.test", 3310)
            connections.append(timeout)
            clock.now += connect
            sock.timeout = timeout
            return sock

        # raising=False lets the red run exercise the original code, which has
        # no monotonic clock yet; the new implementation uses this same boundary.
        monkeypatch.setattr(virus, "monotonic", clock, raising=False)
        monkeypatch.setattr(virus.socket, "create_connection", create_connection)
        return sock, clock, connections

    return install


def scan(data=b"synthetic", timeout=30):
    return virus.clamd_scan(data, "scanner.example.test", 3310, timeout=timeout)


# Resetting the timeout for each reply lets four nine-second fragments pass.
def test_slow_drip_reply_exhausts_aggregate_deadline(scanner):
    sock, clock, _ = scanner(replies=[(9, b"stream"), (9, b": "), (9, b"OK"), (9, b"\0")])
    with pytest.raises(virus.ScannerError):
        scan()
    assert clock.now == 130
    assert sock.closed


# Per-send timeouts incorrectly let a many-chunk upload run longer than 30s.
def test_multi_chunk_upload_exhausts_aggregate_deadline(scanner):
    sock, clock, _ = scanner(sends=[1, 10, 10, 10, 10, 0])
    with pytest.raises(virus.ScannerError):
        scan(b"x" * (3 * 64 * 1024 + 1))
    assert clock.now == 130
    assert len(sock.sent) == 4
    assert sock.receives == 0
    assert sock.closed


# Connection time belongs to the same budget; no send may start after expiry.
@pytest.mark.parametrize("elapsed", [30, 31])
def test_expired_connection_is_closed_before_sending(scanner, elapsed):
    sock, _, _ = scanner(connect=elapsed)
    with pytest.raises(virus.ScannerError):
        scan()
    assert sock.sent == []
    assert sock.closed


# Socket calls can return successfully after the deadline due to scheduling or
# DNS/address behavior. Post-operation checks must deny those late successes.
@pytest.mark.parametrize("elapsed", [30, 31])
def test_successful_send_after_deadline_stops_upload(scanner, elapsed):
    sock, _, _ = scanner(sends=[elapsed], honor_timeout=False)
    with pytest.raises(virus.ScannerError):
        scan()
    assert len(sock.sent) == 1
    assert sock.receives == 0
    assert sock.closed


@pytest.mark.parametrize("reply", [b"stream: OK\0", b"stream: synthetic-reply-canary FOUND\0"])
@pytest.mark.parametrize("elapsed", [30, 31])
def test_late_final_reply_is_not_accepted_or_reflected(scanner, reply, elapsed):
    sock, _, _ = scanner(replies=[(elapsed, reply)], honor_timeout=False)
    with pytest.raises(virus.ScannerError) as result:
        scan()
    assert "synthetic-reply-canary" not in str(result.value)
    assert sock.closed


def test_receive_expiry_stops_before_remaining_fragments(scanner):
    sock, _, _ = scanner(replies=[(30, b"stream: "), (0, b"OK\0")], honor_timeout=False)
    with pytest.raises(virus.ScannerError):
        scan()
    assert sock.receives == 1
    assert sock.closed


# The literal remaining budgets account for connection + command + two data
# chunks + terminator, followed by three successful receive operations.
def test_fragmented_clean_reply_uses_remaining_time_for_every_operation(scanner):
    sock, _, connections = scanner(
        connect=2, sends=[3, 4, 2, 1], replies=[(5, b"stream: "), (4, b"OK"), (3, b"\0")],
    )
    assert scan(b"x" * (64 * 1024 + 1)) is None
    assert connections == [30]
    assert sock.timeouts == [28, 25, 21, 19, 18, 13, 9]
    assert sock.closed


def test_fragmented_infected_reply_preserves_signature(scanner):
    scanner(connect=1, sends=[1, 1, 1], replies=[(1, b"stream: Eicar-"), (1, b"Test FOUND\0")])
    assert scan() == "Eicar-Test"


# Invalid numeric budgets must fail closed before invoking socket APIs, where
# NaN/inf/negative values can otherwise leak ValueError/OverflowError.
@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), float("-inf")],
                         ids=["zero", "negative", "nan", "infinity", "negative-infinity"])
def test_invalid_timeout_fails_closed_without_connection(scanner, timeout):
    _, _, connections = scanner()
    with pytest.raises(virus.ScannerError):
        scan(timeout=timeout)
    assert connections == []
