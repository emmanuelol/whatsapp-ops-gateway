import json
import logging
from whatsapp_ops_gateway.telemetry import (
    GatewayMetrics,
    JsonLogFormatter,
    get_metrics,
)


def test_metrics_singleton():
    m1 = get_metrics()
    m2 = get_metrics()
    assert m1 is m2


def test_metrics_recording():
    metrics = get_metrics()
    metrics.reset()

    assert metrics.get_snapshot()["idempotency_hits_total"] == 0
    metrics.record_idempotency_hit()
    assert metrics.get_snapshot()["idempotency_hits_total"] == 1

    metrics.record_keepalive_failure()
    assert metrics.get_snapshot()["keepalive_failures_total"] == 1

    metrics.record_sanitization_redaction()
    metrics.record_sanitization_redaction()
    assert metrics.get_snapshot()["sanitization_redactions_total"] == 2

    metrics.record_dispatch_attempt("success")
    metrics.record_dispatch_attempt("failure")
    assert metrics.get_snapshot()["dispatch_attempts_total"] == 2

    metrics.record_dispatch_error("timeout")
    assert metrics.get_snapshot()["dispatch_errors_total{cause=\"timeout\"}"] == 1


def test_render_prometheus():
    metrics = get_metrics()
    metrics.reset()
    metrics.record_idempotency_hit()
    metrics.record_keepalive_failure()
    metrics.record_sanitization_redaction()

    rendered = metrics.render_prometheus()
    assert "# TYPE idempotency_hits_total counter" in rendered
    assert "idempotency_hits_total 1" in rendered
    assert "# TYPE keepalive_failures_total counter" in rendered
    assert "keepalive_failures_total 1" in rendered
    assert "# TYPE sanitization_redactions_total counter" in rendered
    assert "sanitization_redactions_total 1" in rendered


def test_json_log_formatter():
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="whatsapp_ops_gateway",
        level=logging.INFO,
        pathname="client.py",
        lineno=42,
        msg="Dispatched payload successfully",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    parsed = json.loads(formatted)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "whatsapp_ops_gateway"
    assert parsed["message"] == "Dispatched payload successfully"
    assert "timestamp" in parsed
