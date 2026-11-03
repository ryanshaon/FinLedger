"""Socket tests for the stdlib Gemini client. Peers are 127.0.0.1 only."""

from __future__ import annotations

import http.client
import inspect
import json
import socket
import threading
import time

import pytest

from usage.model_router import ModelRouter, Settings
from usage.providers.http_client import GeminiHttpClient, open_vertex_https
from usage.providers.http_transport import HttpGeminiTransport, build_generate_url
from usage.providers.transport import TransportError

SECRET = "super-secret-token-do-not-leak"
CANARY = b'{"invoice":"CANARY-BODY-99"}'
LOCATION = "asia-south1"
MODEL = "gemini-1.5-flash"
PROJECT = "finledger-test"


@pytest.fixture
def loopback_only(monkeypatch):
    real = socket.create_connection

    def wrapped(address, *args, **kwargs):
        host = address[0]
        if host != "127.0.0.1" or "googleapis" in str(host):
            raise AssertionError(f"production client was given a non-loopback host: {host}")
        return real(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", wrapped)


def _read_request(conn: socket.socket) -> bytes:
    conn.settimeout(2)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    while len(rest) < length:
        chunk = conn.recv(4096)
        if not chunk:
            break
        rest += chunk
    return head + b"\r\n\r\n" + rest[:length]


def _serve(handler, accept_timeout: float = 3):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    sock.settimeout(accept_timeout)
    port = sock.getsockname()[1]
    box: dict = {"request": b"", "error": None, "accepted": 0}

    def run():
        try:
            conn, _ = sock.accept()
        except OSError as exc:
            box["error"] = exc
            return
        box["accepted"] += 1
        try:
            box["request"] = handler(conn)
        finally:
            try:
                conn.close()
            except OSError:
                pass
            sock.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port, thread, box


def _connect_loopback(port: int):
    def connect(host: str, _port: int, timeout: float):
        if "googleapis" in host:
            # The logical URL names Vertex. The socket must not.
            return http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        raise AssertionError(f"unexpected connect host {host}")

    return connect


def _client(port: int, **kwargs) -> GeminiHttpClient:
    values = {"timeout_seconds": 2, "max_response_bytes": 4096, "connect": _connect_loopback(port)}
    values.update(kwargs)
    return GeminiHttpClient(**values)


def _exchange(client: GeminiHttpClient, body: bytes = CANARY):
    return client.exchange(
        location=LOCATION,
        model=MODEL,
        project=PROJECT,
        headers={"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"},
        body=body,
    )


def test_exchange_has_no_raw_url_argument():
    assert "url" not in inspect.signature(GeminiHttpClient.exchange).parameters


def test_default_connector_does_not_open_a_socket_for_a_non_vertex_host(monkeypatch):
    def exploded(*_args, **_kwargs):
        raise AssertionError("socket opened")

    monkeypatch.setattr("http.client.HTTPSConnection", exploded)
    with pytest.raises(TransportError) as exc:
        open_vertex_https("127.0.0.1", 443, 1.0)
    assert exc.value.code == "invalid_target"
    assert "127.0.0.1" not in str(exc.value)


def test_router_does_not_construct_the_http_client(monkeypatch):
    import usage.model_router as model_router

    source = inspect.getsource(model_router)
    assert "GeminiHttpClient" not in source
    assert "http_client" not in source

    def exploded(*_args, **_kwargs):
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "create_connection", exploded)
    with pytest.raises(ValueError, match="explicit transport"):
        ModelRouter(Settings(provider="gemini", gemini_location=LOCATION))


def test_client_source_does_not_refresh_credentials():
    source = inspect.getsource(GeminiHttpClient)
    module_source = inspect.getsource(inspect.getmodule(GeminiHttpClient))
    assert "google.auth" not in module_source
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in module_source
    assert "url" not in inspect.signature(GeminiHttpClient.exchange).parameters
    assert source


def test_success_and_one_attempt_against_loopback(loopback_only):
    seen = {}

    def handler(conn):
        request = _read_request(conn)
        seen["request"] = request
        body = json.dumps({
            "candidates": [{"content": {"parts": [{"text": "{}"}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4, "totalTokenCount": 7},
        }).encode()
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
        )
        return request

    port, thread, _box = _serve(handler)
    client = _client(port)
    started = time.monotonic()
    status, body = _exchange(client)
    assert time.monotonic() - started < 2
    thread.join(2)
    assert status == 200
    assert b'"text": "{}"' in body or b"candidates" in body
    request = seen["request"]
    assert b"googleapis" not in request
    assert b":generateContent" in request
    assert SECRET.encode() in request
    assert request.count(b"Authorization:") == 1
    assert CANARY in request


def test_transport_uses_the_client_without_an_injected_post(loopback_only):
    def handler(conn):
        request = _read_request(conn)
        body = json.dumps({
            "candidates": [{"content": {"parts": [{"text": "{}"}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4, "totalTokenCount": 7},
        }).encode()
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        return request

    port, thread, box = _serve(handler)
    transport = HttpGeminiTransport(
        credential=SECRET, project=PROJECT, http_client=_client(port),
    )
    response = transport.generate(model=MODEL, location=LOCATION, purpose="extract", payload={"markdown": "x"})
    thread.join(2)
    assert response.tokens_in == 3 and response.tokens_out == 4
    assert b"maxOutputTokens" in box["request"]
    assert build_generate_url(location=LOCATION, model=MODEL, project=PROJECT).encode() not in box["request"]


def test_oversized_stream_stops_during_the_read(loopback_only):
    def handler(conn):
        _read_request(conn)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\n\r\n")
        conn.sendall(b"x" * 200)
        conn.settimeout(2)
        try:
            conn.recv(1)
        except OSError:
            pass
        return b""

    port, thread, _box = _serve(handler)
    client = _client(port, timeout_seconds=5, max_response_bytes=64)
    with pytest.raises(TransportError) as exc:
        _exchange(client)
    assert exc.value.code == "response_too_large"
    assert SECRET not in str(exc.value)
    assert b"CANARY-BODY-99" not in str(exc.value).encode()
    thread.join(2)
    assert not thread.is_alive()


def test_hung_connection_times_out_without_leaking_the_request(loopback_only):
    def handler(conn):
        _read_request(conn)
        time.sleep(5)
        return b""

    port, thread, _box = _serve(handler)
    client = _client(port, timeout_seconds=0.3)
    with pytest.raises(TransportError) as exc:
        _exchange(client)
    assert exc.value.code == "timeout"
    assert SECRET not in str(exc.value)
    assert "CANARY-BODY-99" not in str(exc.value)
    thread.join(0.2)


def test_refused_connection_is_transport_unavailable(loopback_only):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    # Windows needs about two seconds to report refusal; one second tests a timeout instead.
    client = _client(port, timeout_seconds=3)
    with pytest.raises(TransportError) as exc:
        _exchange(client)
    assert exc.value.code == "transport_unavailable"
    assert getattr(exc.value, "body_received", True) is False
    assert SECRET not in str(exc.value)
    assert "CANARY-BODY-99" not in str(exc.value)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_does_not_send_authorization_to_another_host(loopback_only, status):
    hits: list[bytes] = []

    def other(conn):
        hits.append(_read_request(conn))
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
        return hits[-1]

    other_port, other_thread, _other_box = _serve(other, accept_timeout=0.5)

    def first(conn):
        _read_request(conn)
        location = f"http://127.0.0.1:{other_port}/steal".encode()
        conn.sendall(
            f"HTTP/1.1 {status} Found\r\nLocation: ".encode()
            + location
            + b"\r\nContent-Length: 0\r\n\r\n"
        )
        return b""

    port, thread, _box = _serve(first)
    client = _client(port, timeout_seconds=1)
    with pytest.raises(TransportError) as exc:
        _exchange(client)
    assert exc.value.code == "invalid_target"
    message = str(exc.value)
    assert SECRET not in message
    assert "CANARY-BODY-99" not in message
    assert "steal" not in message
    assert "Location" not in message
    assert str(other_port) not in message
    thread.join(1)
    other_thread.join(0.6)
    assert hits == []


def test_client_makes_one_attempt_when_the_status_is_retryable(loopback_only):
    def handler(conn):
        _read_request(conn)
        conn.sendall(b"HTTP/1.1 503 Down\r\nContent-Length: 4\r\n\r\ndown")
        return b""

    port, thread, box = _serve(handler)
    status, body = _exchange(_client(port))
    thread.join(1)
    assert status == 503 and body == b"down"
    assert box["accepted"] == 1


def test_hosted_router_accepts_a_passed_client_without_opening_a_socket(monkeypatch):
    monkeypatch.setenv("FINLEDGER_ENV", "staging")

    def exploded(*_args, **_kwargs):
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "create_connection", exploded)
    client = GeminiHttpClient(connect=exploded)
    transport = HttpGeminiTransport(credential=SECRET, project=PROJECT, http_client=client)
    router = ModelRouter(Settings(provider="gemini", gemini_location=LOCATION), gemini_transport=transport)
    assert router.provider.transport is transport
    assert "GeminiHttpClient(" not in inspect.getsource(inspect.getmodule(ModelRouter))


def test_client_rejects_nonfinite_bounds():
    with pytest.raises(ValueError):
        GeminiHttpClient(timeout_seconds=float("nan"))
    with pytest.raises(ValueError):
        GeminiHttpClient(max_response_bytes=True)
