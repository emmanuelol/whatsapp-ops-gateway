from unittest.mock import MagicMock, patch

import requests

from whatsapp_ops_gateway.client import WhatsAppGatewayClient
from whatsapp_ops_gateway.sanitization import strip_markdown_media
from whatsapp_ops_gateway.taxonomy import DispatchErrorCause
from whatsapp_ops_gateway.telemetry import get_metrics


def _mock_success_response():
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.ok = True
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"ok": True, "result": {"messageId": "msg_test_123"}}
    return resp


def _mock_server_error_response():
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 503
    resp.ok = False
    resp.text = "Service Unavailable"
    resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "503 Server Error", response=resp
    )
    return resp


def test_end_to_end_pipeline_success(isolated_media_dir):
    """
    Verifies full lifecycle orchestration:
    Raw Phone -> Sanitization -> Idempotency -> Fork-Safe Session -> Telemetry -> Response
    """
    metrics = get_metrics()
    metrics.reset()

    # Create dummy media file in shared media dir
    media_file = f"{isolated_media_dir}/chart.png"
    with open(media_file, "w") as f:
        f.write("dummy-png-bytes")

    client = WhatsAppGatewayClient(
        token="ops_secret_token",
        gateway_url="http://mock-openclaw:18789",
        shared_media_dir=isolated_media_dir,
        mock_whatsapp=False,
    )

    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = _mock_success_response()

        raw_phone = "+52 (472) 699-1461"
        raw_message = (
            f"Incident #4490 report: ![Chart]({isolated_media_dir}/chart.png) All nodes restored."
        )
        expected_cleaned = strip_markdown_media(raw_message, isolated_media_dir)

        result = client.send_message(
            phone=raw_phone,
            message=raw_message,
            media_path=media_file,
            _is_system_alert=True,
        )

        assert result is True
        assert client.last_error is None
        assert mock_post.call_count == 1

        # Verify dispatched payload structure
        call_kwargs = mock_post.call_args[1]
        sent_payload = call_kwargs["json"]
        assert sent_payload["tool"] == "message"
        assert sent_payload["action"] == "send"
        args = sent_payload["args"]
        assert args["target"] == "+5214726991461"
        assert args["message"] == expected_cleaned
        assert args["media"] == media_file
        assert "idempotencyKey" in args
        assert len(args["idempotencyKey"]) == 64

        # Verify telemetry metric tracking
        snapshot = metrics.get_snapshot()
        assert snapshot["dispatch_attempts_total"] == 1
        assert snapshot["sanitization_redactions_total"] == 0


def test_end_to_end_ux_leak_interception():
    metrics = get_metrics()
    metrics.reset()

    client = WhatsAppGatewayClient(token="valid_token", mock_whatsapp=False)

    with patch("requests.Session.post") as mock_post:
        leaked_msg = "Please verify private key at /tmp/secret.pem"
        result = client.send_message("+5215512345678", leaked_msg)

        assert result is False
        assert client.last_error == DispatchErrorCause.UX_LEAK.value
        mock_post.assert_not_called()

        # Telemetry should capture both the redaction and classified error
        snapshot = metrics.get_snapshot()
        assert snapshot["sanitization_redactions_total"] == 1
        assert snapshot['dispatch_errors_total{cause="ux_leak"}'] == 1


def test_end_to_end_retry_exhaustion_telemetry():
    """
    Verifies that when upstream returns 503, tenacity retries 3 times,
    all requests maintain the exact same idempotency key, and failure is recorded in telemetry.
    """
    metrics = get_metrics()
    metrics.reset()

    client = WhatsAppGatewayClient(token="valid_token", mock_whatsapp=False)

    with patch("time.sleep", return_value=None), patch("requests.Session.post") as mock_post:
        mock_post.side_effect = [
            _mock_server_error_response(),
            _mock_server_error_response(),
            _mock_server_error_response(),
        ]

        result = client.send_message("+5215512345678", "High priority alert")

        assert result is False
        assert client.last_error == DispatchErrorCause.HTTP_SERVER.value
        assert mock_post.call_count == 3

        # Verify all 3 retried calls share identical idempotency key
        keys = {call[1]["json"]["args"]["idempotencyKey"] for call in mock_post.call_args_list}
        assert len(keys) == 1

        # Verify telemetry recorded 1 dispatch attempt and 1 http_server error
        snapshot = metrics.get_snapshot()
        assert snapshot["dispatch_attempts_total"] == 1
        assert snapshot['dispatch_errors_total{cause="http_server"}'] == 1
