import os
import time
import random
import json
import logging
from typing import Optional, Callable, Any
import requests
import requests.adapters

from whatsapp_ops_gateway.client import WhatsAppGatewayClient
from whatsapp_ops_gateway.sanitization import sanitize_phone

logger = logging.getLogger(__name__)


class GatewayKeepalive:
    """
    Watchdog and keepalive manager for OpenClaw Gateway and WhatsApp WebSocket session.

    Performs:
    1. Host loopback bridge probe (/health) with 3-failure threshold watchdog.
    2. Gateway HTTP HEAD liveness ping with pooled session.
    3. Self-targeted WhatsApp frame on 12-25m jitter interval to prevent Meta 30m idle disconnects.
    """

    def __init__(
        self,
        state_file: str,
        bridge_url: Optional[str] = None,
        gateway_url: Optional[str] = None,
        bot_phone: Optional[str] = None,
        client: Optional[WhatsAppGatewayClient] = None,
        alert_callback: Optional[Callable[[str], Any]] = None,
    ):
        self.state_file = state_file
        self.bridge_url = bridge_url or os.getenv("KIOSK_BRIDGE_URL", "http://127.0.0.1:8080/health")
        self.gateway_url = (gateway_url or os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")).rstrip("/")
        self.bot_phone = bot_phone or os.getenv("BOT_PHONE") or os.getenv("OPENCLAW_BOT_PHONE")
        self.client = client or WhatsAppGatewayClient()
        self.alert_callback = alert_callback

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_state(self, state: dict) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.state_file)), exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.error(f"Failed to save keepalive state: {e}")

    def check_bridge_health(self) -> bool:
        """
        Probes loopback bridge /health. If 3 consecutive failures occur,
        triggers the alert callback once until health recovers.
        """
        state = self._load_state()
        bridge_failures = state.get("bridge_failures", 0)
        alert_sent = state.get("bridge_alert_sent", False)
        is_healthy = False

        try:
            resp = requests.get(self.bridge_url, timeout=5.0)
            if resp.status_code == 200:
                is_healthy = True
                bridge_failures = 0
                alert_sent = False
                logger.info(f"Bridge probe {self.bridge_url} OK.")
            else:
                logger.warning(f"Bridge probe {self.bridge_url} returned HTTP {resp.status_code}")
                bridge_failures += 1
        except Exception as e:
            logger.warning(f"Bridge probe {self.bridge_url} failed: {e}")
            bridge_failures += 1

        if bridge_failures >= 3 and not alert_sent:
            alert_msg = (
                f"🚨 CRITICAL SRE: Bridge {self.bridge_url} failed 3 consecutive checks. "
                "Gateway host tools unreachable."
            )
            if self.alert_callback:
                try:
                    self.alert_callback(alert_msg)
                except Exception:
                    pass
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
        next_run = state.get("next_run", 0.0)

        if not force and now < next_run:
            logger.info("Keepalive skipped: interval not yet reached.")
            return True

        # HTTP Liveness Ping with pooled session
        target = f"{self.gateway_url}/"
        try:
            with requests.Session() as session:
                adapter = requests.adapters.HTTPAdapter(max_retries=1, pool_connections=1, pool_maxsize=1)
                session.mount("http://", adapter)
                session.mount("https://", adapter)
                resp = session.head(target, timeout=(3.0, 10.0))
                if resp.status_code < 500:
                    logger.info(f"Gateway HTTP Head probe OK (HTTP {resp.status_code}).")
                else:
                    logger.warning(f"Gateway HTTP Head probe degraded (HTTP {resp.status_code}).")
        except Exception as e:
            logger.error(f"Gateway HTTP Head probe failed: {e}")

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
