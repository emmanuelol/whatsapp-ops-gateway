import re
from datetime import datetime, timezone
import pytest

try:
    from hypothesis import given, strategies as st
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

from whatsapp_ops_gateway.sanitization import (
    sanitize_phone,
    resolve_media_path,
    strip_markdown_media,
    detect_ux_leak,
)
from whatsapp_ops_gateway.idempotency import compute_idempotency_key


@pytest.mark.skipif(not HAS_HYPOTHESIS, reason="Hypothesis not installed")
@given(st.text())
def test_fuzz_sanitize_phone_never_crashes(raw_phone: str):
    res = sanitize_phone(raw_phone)
    assert isinstance(res, str)
    if res:
        assert res.startswith("+")
        assert res[1:].isdigit()


@pytest.mark.skipif(not HAS_HYPOTHESIS, reason="Hypothesis not installed")
@given(
    phone=st.text(min_size=1, max_size=50),
    message=st.text(max_size=500),
    media_path=st.one_of(st.none(), st.text(max_size=100)),
)
def test_fuzz_compute_idempotency_key_deterministic(phone: str, message: str, media_path: str | None):
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    k1 = compute_idempotency_key(phone, message, media_path=media_path, now=now)
    k2 = compute_idempotency_key(phone, message, media_path=media_path, now=now)
    assert k1 == k2
    assert len(k1) == 64
    assert bool(re.fullmatch(r"[0-9a-f]{64}", k1))


@pytest.mark.skipif(not HAS_HYPOTHESIS, reason="Hypothesis not installed")
@given(text=st.text(), shared_dir=st.text(min_size=1, max_size=100))
def test_fuzz_strip_markdown_media_never_crashes(text: str, shared_dir: str):
    res = strip_markdown_media(text, shared_dir)
    assert isinstance(res, str)


@pytest.mark.skipif(not HAS_HYPOTHESIS, reason="Hypothesis not installed")
@given(text=st.text(), shared_dir=st.text(min_size=1, max_size=100))
def test_fuzz_detect_ux_leak_never_crashes(text: str, shared_dir: str):
    res = detect_ux_leak(text, shared_dir)
    assert isinstance(res, bool)
