import os
import json
import pytest
from unittest.mock import patch, MagicMock
from whatsapp_ops_gateway.keepalive import GatewayKeepalive


def test_bridge_health_success(tmp_path):
    state_file = str(tmp_path / "keepalive_state.json")
    keepalive = GatewayKeepalive(state_file=state_file, bridge_url="http://127.0.0.1:8080/health")

    mock_resp = MagicMock(status_code=200)
    with patch("requests.get", return_value=mock_resp):
        assert keepalive.check_bridge_health() is True

    with open(state_file, "r") as f:
        data = json.load(f)
    assert data["bridge_failures"] == 0
    assert data["bridge_alert_sent"] is False


def test_bridge_health_alert_on_three_consecutive_failures(tmp_path):
    state_file = str(tmp_path / "keepalive_state.json")
    alert_callback = MagicMock()
    keepalive = GatewayKeepalive(
        state_file=state_file,
        bridge_url="http://127.0.0.1:8080/health",
        alert_callback=alert_callback,
    )

    mock_fail_resp = MagicMock(status_code=502)
    with patch("requests.get", return_value=mock_fail_resp):
        # 1st failure
        assert keepalive.check_bridge_health() is False
        assert alert_callback.call_count == 0

        # 2nd failure
        assert keepalive.check_bridge_health() is False
        assert alert_callback.call_count == 0

        # 3rd failure: must trigger alert callback exactly once
        assert keepalive.check_bridge_health() is False
        assert alert_callback.call_count == 1

        # 4th failure: should NOT re-alert
        assert keepalive.check_bridge_health() is False
        assert alert_callback.call_count == 1

    # Recovery check
    mock_ok_resp = MagicMock(status_code=200)
    with patch("requests.get", return_value=mock_ok_resp):
        assert keepalive.check_bridge_health() is True

    with open(state_file, "r") as f:
        data = json.load(f)
    assert data["bridge_failures"] == 0
    assert data["bridge_alert_sent"] is False


def test_keepalive_ping_flow(tmp_path):
    state_file = str(tmp_path / "keepalive_state.json")
    mock_client = MagicMock()
    mock_client.send_message.return_value = True

    keepalive = GatewayKeepalive(
        state_file=state_file,
        client=mock_client,
        gateway_url="http://127.0.0.1:18789",
        bot_phone="+5215512345678",
    )

    mock_head_resp = MagicMock(status_code=200)
    with patch("requests.Session.head", return_value=mock_head_resp), \
         patch("requests.get", return_value=MagicMock(status_code=200)):
        keepalive.ping(force=True)

    assert mock_client.send_message.call_count == 1
    args, kwargs = mock_client.send_message.call_args
    assert args[0] == "+5215512345678"
    assert "Keepalive" in args[1]
    assert kwargs.get("_is_system_alert") is True
    assert "keepalive:+5215512345678:" in kwargs.get("idempotency_key")

    with open(state_file, "r") as f:
        data = json.load(f)
    assert data["next_run"] > 0
    assert 12 <= data["delay_minutes"] <= 25
