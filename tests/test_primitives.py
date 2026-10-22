import os
import threading
from datetime import datetime, timezone
import requests
import pytest

from whatsapp_ops_gateway.taxonomy import DispatchErrorCause
from whatsapp_ops_gateway.session import (
    get_fork_safe_session,
    _reset_session_after_fork,
)
from whatsapp_ops_gateway.idempotency import (
    compute_idempotency_key,
    compute_interactive_key,
)
from whatsapp_ops_gateway.sanitization import (
    sanitize_phone,
    resolve_media_path,
    strip_markdown_media,
    detect_ux_leak,
)


# --- 1. Session & Fork Isolation Tests ---

def test_session_returns_requests_session_with_zero_transport_retries():
    session = get_fork_safe_session()
    assert isinstance(session, requests.Session)
    adapter = session.adapters.get("http://")
    assert adapter is not None
    # max_retries must be 0 to prevent multiplicative retry amplification
    assert getattr(adapter.max_retries, "total", adapter.max_retries) == 0


def test_session_thread_safe_singleton_per_process():
    sessions = []

    def worker():
        sessions.append(get_fork_safe_session())

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All threads in the same process must share the exact same session instance
    for s in sessions:
        assert s is sessions[0]


def test_session_resets_after_fork_simulation(monkeypatch):
    s1 = get_fork_safe_session()
    original_pid = os.getpid()

    # Simulate child process after fork
    _reset_session_after_fork()
    monkeypatch.setattr(os, "getpid", lambda: original_pid + 999)

    s2 = get_fork_safe_session()
    assert s1 is not s2

    # Clean up
    _reset_session_after_fork()


# --- 2. Idempotency Key Generation Tests ---

def test_compute_idempotency_key_is_deterministic_for_same_payload():
    dt = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    k1 = compute_idempotency_key("+5215512345678", "Hello world", now=dt)
    k2 = compute_idempotency_key("+5215512345678", "Hello world", now=dt)
    assert k1 == k2
    assert len(k1) == 64  # sha256 hex


def test_compute_idempotency_key_distinguishes_media_only_sends():
    dt = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    k1 = compute_idempotency_key("+5215512345678", "", media_path="/media/a.png", now=dt)
    k2 = compute_idempotency_key("+5215512345678", "", media_path="/media/b.png", now=dt)
    assert k1 != k2


def test_compute_idempotency_key_rotates_across_time_windows():
    dt1 = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    dt2 = datetime(2026, 10, 22, 12, 11, 0, tzinfo=timezone.utc)  # +11 minutes (> 600s)
    k1 = compute_idempotency_key("+5215512345678", "Keepalive Ping", now=dt1)
    k2 = compute_idempotency_key("+5215512345678", "Keepalive Ping", now=dt2)
    assert k1 != k2


def test_compute_interactive_key():
    k1 = compute_interactive_key("+5215512345678", prefix="cfo_http", date_str="2026-10-22", entropy="uuid-1")
    k2 = compute_interactive_key("+5215512345678", prefix="cfo_http", date_str="2026-10-22", entropy="uuid-2")
    assert k1 != k2
    assert len(k1) == 64


# --- 3. Sanitization & UX Leak Tests ---

def test_sanitize_phone():
    # 10 digits
    assert sanitize_phone("4726991461") == "+5214726991461"
    # 12 digits starting with 52
    assert sanitize_phone("+524726991461") == "+5214726991461"
    # Already +521
    assert sanitize_phone("+521 472 699 1461") == "+5214726991461"
    # Empty or invalid
    assert sanitize_phone("") == ""
    assert sanitize_phone(None) == ""


def test_resolve_media_path():
    shared_dir = "/shared_media"
    assert resolve_media_path("/app/shared_media/chart.png", shared_dir) == "/shared_media/chart.png"
    assert resolve_media_path("/shared_media/chart.png", shared_dir) == "/shared_media/chart.png"
    assert resolve_media_path("/etc/passwd", shared_dir) is None
    assert resolve_media_path(None, shared_dir) is None


def test_strip_markdown_media():
    shared_dir = "/shared_media"
    msg = "Report: ![Chart](/shared_media/chart.png) End."
    cleaned = strip_markdown_media(msg, shared_dir)
    assert cleaned == "Report:End."


def test_detect_ux_leak():
    shared_dir = "/shared_media"
    assert detect_ux_leak("Hello world", shared_dir) is False
    assert detect_ux_leak("Check https://example.com/image.png", shared_dir) is False
    assert detect_ux_leak("Here is <media> something", shared_dir) is True
    assert detect_ux_leak("File at /tmp/secret.json", shared_dir) is True
    assert detect_ux_leak("Bad image: ![x](/var/log/app.log)", shared_dir) is True


# --- 4. Taxonomy Constants Tests ---

def test_dispatch_error_cause_taxonomy():
    assert DispatchErrorCause.INVALID_PHONE.value == "invalid_phone"
    assert DispatchErrorCause.UX_LEAK.value == "ux_leak"
    assert DispatchErrorCause.MISSING_TOKEN.value == "missing_token"
    assert DispatchErrorCause.SESSION_OFFLINE.value == "session_offline"
    assert DispatchErrorCause.HTTP_AUTH.value == "http_auth"
    assert DispatchErrorCause.HTTP_SERVER.value == "http_server"
    assert DispatchErrorCause.INVALID_JSON.value == "invalid_json"
    assert DispatchErrorCause.TIMEOUT.value == "timeout"
    assert DispatchErrorCause.SSL_ERROR.value == "ssl_error"
    assert DispatchErrorCause.CONN_ERROR.value == "conn_error"
    assert DispatchErrorCause.REQUEST_ERROR.value == "request_error"
    assert DispatchErrorCause.GATEWAY_REJECTED.value == "gateway_rejected"
    assert DispatchErrorCause.UNKNOWN_ERROR.value == "unknown_error"
