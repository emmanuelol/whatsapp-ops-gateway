import os
import pytest
from unittest.mock import patch, MagicMock
import requests

from whatsapp_ops_gateway.client import WhatsAppGatewayClient, WhatsAppService
from whatsapp_ops_gateway.taxonomy import DispatchErrorCause


def _ok_response(data=None):
    resp = MagicMock()
    resp.status_code = 200
    resp.ok = True
    resp.raise_for_status.return_value = None
    resp.json.return_value = data if data is not None else {"ok": True}
    return resp


def _fail_http_response(status_code=500, text="Internal Server Error"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = False
    resp.text = text
    resp.raise_for_status.side_effect = requests.exceptions.HTTPError(f"HTTP {status_code}: {text}", response=resp)
    return resp


# --- 1. Basic Dispatch & Aliasing ---

def test_service_alias_matches_client():
    assert WhatsAppService is WhatsAppGatewayClient


@patch("requests.Session.post")
def test_successful_dispatch(mock_post):
    mock_post.return_value = _ok_response()
    client = WhatsAppGatewayClient(token="test_tok", mock_whatsapp=False)
    result = client.send_message("+5215512345678", "Hello world")
    assert result is True
    assert client.last_error is None
    assert mock_post.call_count == 1
    payload = mock_post.call_args[1]["json"]
    assert payload["args"]["target"] == "+5215512345678"
    assert payload["args"]["message"] == "Hello world"
    assert "idempotencyKey" in payload["args"]


# --- 2. Kill-Switch Gatekeeper ---

@patch("requests.Session.post")
def test_kill_switch_suppresses_proactive_dispatch(mock_post):
    client = WhatsAppGatewayClient(token="test_tok", enable_automated_dispatches=False, mock_whatsapp=False)
    assert client.send_message("+5215512345678", "Automated daily notification") is True
    mock_post.assert_not_called()


@pytest.mark.parametrize("kwargs", [{"_is_system_alert": True}, {"interactive": True}])
@patch("requests.Session.post")
def test_kill_switch_exempts_system_alerts_and_interactive(mock_post, kwargs):
    mock_post.return_value = _ok_response()
    client = WhatsAppGatewayClient(token="test_tok", enable_automated_dispatches=False, mock_whatsapp=False)
    assert client.send_message("+5215512345678", "Priority message", **kwargs) is True
    mock_post.assert_called_once()


# --- 3. Mock Blast Shield ---

@patch("requests.Session.post")
def test_mock_whatsapp_intercepts_dispatch(mock_post):
    client = WhatsAppGatewayClient(token="test_tok", mock_whatsapp=True)
    assert client.send_message("+5215512345678", "Mocked text") is True
    mock_post.assert_not_called()


@patch("requests.Session.post")
def test_mock_whatsapp_force_real_bypasses_mock(mock_post):
    mock_post.return_value = _ok_response()
    client = WhatsAppGatewayClient(token="test_tok", mock_whatsapp=True)
    assert client.send_message("+5215512345678", "Real text", force_real=True) is True
    mock_post.assert_called_once()


# --- 4. Tenacity Retries and Key Stability ---

@patch("time.sleep", return_value=None)
@patch("requests.Session.post")
def test_tenacity_retries_reuse_same_idempotency_key(mock_post, _sleep):
    mock_post.side_effect = [
        _fail_http_response(503, "Service Unavailable"),
        _fail_http_response(502, "Bad Gateway"),
        _ok_response(),
    ]
    client = WhatsAppGatewayClient(token="test_tok", mock_whatsapp=False)
    assert client.send_message("+5215512345678", "Retry testing") is True
    assert mock_post.call_count == 3
    keys = {call[1]["json"]["args"]["idempotencyKey"] for call in mock_post.call_args_list}
    assert len(keys) == 1


# --- 5. Strict Identity Check & Rejection ---

@pytest.mark.parametrize("bad_body", [
    {"ok": False, "error": "Quota exceeded"},
    {"ok": "true"},     # String "true" must NOT pass
    {"ok": 1},          # Truthy integer 1 must NOT pass
    {"success": True},  # Different key
    {},                 # Empty dict
    ["ok"],             # Non-dict JSON list
])
@patch("requests.Session.post")
def test_gateway_200_without_strict_bool_true_returns_false(mock_post, bad_body):
    mock_post.return_value = _ok_response(bad_body)
    client = WhatsAppGatewayClient(token="test_tok", mock_whatsapp=False)
    result = client.send_message("+5215512345678", "Payload")
    assert result is False
    assert client.last_error in (DispatchErrorCause.GATEWAY_REJECTED.value, DispatchErrorCause.INVALID_JSON.value)


# --- 6. Error Taxonomy ---

@pytest.mark.parametrize("error_cause,setup_action", [
    ("invalid_phone", lambda m: None),
    ("ux_leak", lambda m: None),
    ("missing_token", lambda m: None),
    ("session_offline", lambda m: setattr(m, "return_value", _fail_http_response(500, "No active WhatsApp Web listener"))),
    ("http_auth", lambda m: setattr(m, "return_value", _fail_http_response(401, "Unauthorized"))),
    ("http_server", lambda m: setattr(m, "return_value", _fail_http_response(503, "Service Unavailable"))),
    ("timeout", lambda m: setattr(m, "side_effect", requests.exceptions.ConnectTimeout("timeout"))),
    ("ssl_error", lambda m: setattr(m, "side_effect", requests.exceptions.SSLError("cert error"))),
    ("conn_error", lambda m: setattr(m, "side_effect", requests.exceptions.ConnectionError("refused"))),
    ("request_error", lambda m: setattr(m, "side_effect", requests.exceptions.RequestException("failed"))),
    ("gateway_rejected", lambda m: setattr(m, "return_value", _ok_response({"ok": False}))),
    ("unknown_error", lambda m: setattr(m, "side_effect", RuntimeError("unexpected crash"))),
])
@patch("time.sleep", return_value=None)
@patch("requests.Session.post")
def test_send_message_error_taxonomy(mock_post, mock_sleep, error_cause, setup_action):
    setup_action(mock_post)
    token = "" if error_cause == "missing_token" else "valid_token"
    client = WhatsAppGatewayClient(token=token, mock_whatsapp=False)

    phone = "+5215512345678"
    msg = "Test"
    if error_cause == "invalid_phone":
        phone = ""
    elif error_cause == "ux_leak":
        msg = "Leaked <media> tag"

    result = client.send_message(phone, msg)
    assert result is False
    assert client.last_error == error_cause


@patch("requests.Session.post")
def test_success_resets_last_error(mock_post):
    mock_post.return_value = _ok_response()
    client = WhatsAppGatewayClient(token="valid_token", mock_whatsapp=False)

    # First trigger invalid_phone
    assert client.send_message("", "test") is False
    assert client.last_error == DispatchErrorCause.INVALID_PHONE.value

    # Second is valid
    assert client.send_message("+5215512345678", "test") is True
    assert client.last_error is None
