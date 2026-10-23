import contextlib
import json
import logging
import os
import random
import time
from collections.abc import Callable
from typing import Any

import requests
import requests.adapters

from whatsapp_ops_gateway.client import WhatsAppGatewayClient
from whatsapp_ops_gateway.sanitization import sanitize_phone
from whatsapp_ops_gateway.telemetry import GatewayMetrics, get_metrics

logger = logging.getLogger(__name__)


class GatewayKeepalive:
    """
    Watchdog and keepalive manager for OpenClaw Gateway and WhatsApp WebSocket session.

    Performs:
    1. Host loopback bridge probe (/health) with 3-failure threshold watchdog.
    2. Gateway HTTP HEAD liveness ping with pooled session.
    3. Self-targeted WhatsApp frame on 12-25m jitter interval to prevent Meta 30m idle disconnects.
    4. First-class telemetry instrumentation on health degradation and probe failures.
    """

    def __init__(
        self,
        state_file: str,
        bridge_url: str | None = None,
        gateway_url: str | None = None,
        bot_phone: str | None = None,
        client: WhatsAppGatewayClient | None = None,
        alert_callback: Callable[[str], Any] | None = None,
        metrics: GatewayMetrics | None = None,
    ):
        self.state_file = state_file
        if bridge_url is not None:
            self.bridge_url = bridge_url
        else:
            self.bridge_url = os.getenv("KIOSK_BRIDGE_URL") or "http://127.0.0.1:8080/health"

        if gateway_url is not None:
            self.gateway_url = gateway_url.rstrip("/")
        else:
            self.gateway_url = (
                os.getenv("OPENCLAW_GATEWAY_URL") or "http://127.0.0.1:18789"
            ).rstrip("/")

        self.bot_phone: str | None = (
            bot_phone or os.getenv("BOT_PHONE") or os.getenv("OPENCLAW_BOT_PHONE")
        )
        self.client: WhatsAppGatewayClient = client or WhatsAppGatewayClient()
        self.alert_callback = alert_callback
        self.metrics: GatewayMetrics = metrics or get_metrics()

    def _load_state(self) -> dict[str, Any]:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return dict(data)
                    return {}
            except Exception:
                return {}
        return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.state_file)), exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.error("Failed to save keepalive state: %s", e)

    def check_bridge_health(self) -> bool:
        """
        Probes loopback bridge /health. If 3 consecutive failures occur,
        triggers the alert callback once until health recovers.
        """
        state = self._load_state()
        bridge_failures: int = int(state.get("bridge_failures", 0))
        alert_sent: bool = bool(state.get("bridge_alert_sent", False))
        is_healthy = False

        try:
            resp = requests.get(self.bridge_url, timeout=5.0)
            if resp.status_code == 200:
                is_healthy = True
                bridge_failures = 0
                alert_sent = False
                logger.info("Bridge probe %s OK.", self.bridge_url)
            else:
                logger.warning(
                    "Bridge probe %s returned HTTP %s", self.bridge_url, resp.status_code
                )
                bridge_failures += 1
                self.metrics.record_keepalive_failure()
        except Exception as e:
            logger.warning("Bridge probe %s failed: %s", self.bridge_url, e)
            bridge_failures += 1
            self.metrics.record_keepalive_failure()

        if bridge_failures >= 3 and not alert_sent:
            alert_msg = (
                f"🚨 CRITICAL SRE: Bridge {self.bridge_url} failed 3 consecutive checks. "
                "Gateway host tools unreachable."
            )
            if self.alert_callback:
                with contextlib.suppress(Exception):
                    self.alert_callback(alert_msg)
            alert_sent = True

        state["bridge_failures"] = bridge_failures
        state["bridge_alert_sent"] = alert_sent
        self._save_state(state)
        return is_healthy

    def ping(self, force: bool = False) -> bool:
        """
        Executes keepalive loop: bridge check, gateway HTTP probe, and WhatsApp self-ping frame.
        """
        now = time.time()
        self.check_bridge_health()

        state = self._load_state()
        next_run: float = float(state.get("next_run", 0.0))

        if not force and now < next_run:
            logger.info("Keepalive skipped: interval not yet reached.")
            return True

        # HTTP Liveness Ping with pooled session
        target = f"{self.gateway_url}/"
        try:
            with requests.Session() as session:
                adapter = requests.adapters.HTTPAdapter(
                    max_retries=1, pool_connections=1, pool_maxsize=1
                )
                session.mount("http://", adapter)
                session.mount("https://", adapter)
                resp = session.head(target, timeout=(3.0, 10.0))
                if resp.status_code < 500:
                    logger.info("Gateway HTTP Head probe OK (HTTP %s).", resp.status_code)
                else:
                    logger.warning("Gateway HTTP Head probe degraded (HTTP %s).", resp.status_code)
                    self.metrics.record_keepalive_failure()
        except Exception as e:
            logger.error("Gateway HTTP Head probe failed: %s", e)
            self.metrics.record_keepalive_failure()

        # WhatsApp WebSocket self-ping frame
        if not self.bot_phone:
            logger.warning("No BOT_PHONE configured; skipping WhatsApp keepalive frame.")
            return False

        clean_phone = sanitize_phone(str(self.bot_phone))
        if clean_phone:
            msg = "💓 OpenClaw Gateway Keepalive (Self-Ping)"
            window_bucket = int(time.time() // 600)
            idem_key = f"keepalive:{clean_phone}:{window_bucket}"
            self.client.send_message(
                clean_phone,
                msg,
                idempotency_key=idem_key,
                _is_system_alert=True,
            )

        # Schedule next interval: 12m to 25m jitter
        delay_seconds = random.randint(720, 1500)
        state["next_run"] = now + delay_seconds
        state["last_run"] = now
        state["delay_minutes"] = delay_seconds // 60
        self._save_state(state)
        return True
