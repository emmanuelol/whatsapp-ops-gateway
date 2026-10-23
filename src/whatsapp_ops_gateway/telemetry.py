"""
whatsapp-ops-gateway: Observability & Telemetry Primitives

Provides thread-safe in-memory metric counters, Prometheus text exposition,
and structured JSON log formatting for SRE observability pipelines.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from typing import Any


class GatewayMetrics:
    """Thread-safe metric registry and aggregator for gateway operations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idempotency_hits_total: int = 0
        self._keepalive_failures_total: int = 0
        self._sanitization_redactions_total: int = 0
        self._dispatch_attempts_total: int = 0
        self._dispatch_errors_total: dict[str, int] = {}

    def record_idempotency_hit(self) -> None:
        """Increment count of prevented duplicate dispatches."""
        with self._lock:
            self._idempotency_hits_total += 1

    def record_keepalive_failure(self) -> None:
        """Increment count of keepalive handshake or ping failures."""
        with self._lock:
            self._keepalive_failures_total += 1

    def record_sanitization_redaction(self) -> None:
        """Increment count of intercepted UX or path leaks."""
        with self._lock:
            self._sanitization_redactions_total += 1

    def record_dispatch_attempt(self, status: str = "attempt") -> None:
        """Increment total message dispatch attempts."""
        with self._lock:
            self._dispatch_attempts_total += 1

    def record_dispatch_error(self, cause: str) -> None:
        """Increment errors partitioned by canonical taxonomy cause."""
        with self._lock:
            self._dispatch_errors_total[cause] = self._dispatch_errors_total.get(cause, 0) + 1

    def get_snapshot(self) -> dict[str, int]:
        """Return point-in-time dictionary snapshot of all metrics."""
        with self._lock:
            snapshot: dict[str, int] = {
                "idempotency_hits_total": self._idempotency_hits_total,
                "keepalive_failures_total": self._keepalive_failures_total,
                "sanitization_redactions_total": self._sanitization_redactions_total,
                "dispatch_attempts_total": self._dispatch_attempts_total,
            }
            for cause, count in self._dispatch_errors_total.items():
                snapshot[f'dispatch_errors_total{{cause="{cause}"}}'] = count
            return snapshot

    def render_prometheus(self) -> str:
        """Render metrics in standard Prometheus text-based exposition format."""
        snapshot = self.get_snapshot()
        lines = [
            "# HELP idempotency_hits_total Total duplicate dispatches prevented by idempotency",
            "# TYPE idempotency_hits_total counter",
            f"idempotency_hits_total {snapshot['idempotency_hits_total']}",
            "# HELP keepalive_failures_total Total keepalive handshake failures",
            "# TYPE keepalive_failures_total counter",
            f"keepalive_failures_total {snapshot['keepalive_failures_total']}",
            "# HELP sanitization_redactions_total Total intercepted internal path or UX leaks",
            "# TYPE sanitization_redactions_total counter",
            f"sanitization_redactions_total {snapshot['sanitization_redactions_total']}",
            "# HELP dispatch_attempts_total Total message dispatch attempts",
            "# TYPE dispatch_attempts_total counter",
            f"dispatch_attempts_total {snapshot['dispatch_attempts_total']}",
        ]
        if self._dispatch_errors_total:
            lines.extend(
                [
                    "# HELP dispatch_errors_total Total dispatch failures classified by error cause",
                    "# TYPE dispatch_errors_total counter",
                ]
            )
            for cause, count in sorted(self._dispatch_errors_total.items()):
                lines.append(f'dispatch_errors_total{{cause="{cause}"}} {count}')
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Reset all counters to zero (used in tests or session recycle)."""
        with self._lock:
            self._idempotency_hits_total = 0
            self._keepalive_failures_total = 0
            self._sanitization_redactions_total = 0
            self._dispatch_attempts_total = 0
            self._dispatch_errors_total.clear()


_METRICS_INSTANCE: GatewayMetrics | None = None
_METRICS_INIT_LOCK = threading.Lock()


def get_metrics() -> GatewayMetrics:
    """Return process-wide GatewayMetrics singleton."""
    global _METRICS_INSTANCE
    if _METRICS_INSTANCE is None:
        with _METRICS_INIT_LOCK:
            if _METRICS_INSTANCE is None:
                _METRICS_INSTANCE = GatewayMetrics()
    return _METRICS_INSTANCE


class JsonLogFormatter(logging.Formatter):
    """Structured JSON formatter for production log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "source": f"{record.pathname}:{record.lineno}",
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
