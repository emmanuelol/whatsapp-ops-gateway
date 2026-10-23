import os
import json
import logging
from typing import Optional, Callable, Any, Dict
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, RetryCallState
import requests
import urllib3.exceptions

from whatsapp_ops_gateway.session import get_fork_safe_session
from whatsapp_ops_gateway.idempotency import compute_idempotency_key
from whatsapp_ops_gateway.sanitization import (
    sanitize_phone,
    resolve_media_path,
    strip_markdown_media,
    detect_ux_leak,
)
from whatsapp_ops_gateway.taxonomy import DispatchErrorCause
from whatsapp_ops_gateway.telemetry import GatewayMetrics, get_metrics

logger = logging.getLogger(__name__)


def _log_retry_attempt(retry_state: RetryCallState) -> None:
    sleep_time = getattr(retry_state.next_action, "sleep", 0.0) if retry_state.next_action else 0.0
    logger.warning("OpenClaw unreachable, retrying in %.2f seconds...", sleep_time)


class WhatsAppGatewayClient:
    """
    SRE-hardened client for dispatching WhatsApp messages via OpenClaw REST gateway.

    Features:
    - Fork-safe HTTP connection pooling (os.register_at_fork).
    - Deterministic 10-minute quantized idempotency keys.
    - Centralized policy kill-switch (ENABLE_AUTOMATED_DISPATCHES) with system alert and interactive exemptions.
    - Zero multiplicative retry amplification (transport retries=0, tenacity retries=3).
    - Strict boolean validation on gateway response (ok is True).
    - 13-cause structured error taxonomy.
    - First-class observability integration with GatewayMetrics.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        gateway_url: Optional[str] = None,
        shared_media_dir: Optional[str] = None,
        enable_automated_dispatches: Optional[bool] = None,
        mock_whatsapp: Optional[bool] = None,
        alert_callback: Optional[Callable[[str], Any]] = None,
        metrics: Optional[GatewayMetrics] = None,
    ):
        raw_token = token if token is not None else os.getenv("OPENCLAW_TOKEN", "")
        self.token: str = raw_token.strip().strip("'\"")

        if gateway_url is not None:
            self.gateway_url = gateway_url.rstrip("/")
        else:
            bot_host = os.getenv("OPENCLAW_BOT_CONTAINER", "openclaw_bot")
            default_gateway = os.getenv("OPENCLAW_GATEWAY_URL") or f"http://{bot_host}:18789"
            self.gateway_url = default_gateway.rstrip("/")

        if shared_media_dir is not None:
            self.shared_media_dir = shared_media_dir.rstrip("/")
        else:
            self.shared_media_dir = (os.getenv("SHARED_MEDIA_DIR") or "/shared_media").rstrip("/")

        if enable_automated_dispatches is not None:
            self.enable_automated_dispatches: bool = enable_automated_dispatches
        else:
            env_val = os.getenv("ENABLE_AUTOMATED_DISPATCHES", "true").lower()
            self.enable_automated_dispatches = env_val in ("1", "true", "yes")

        if mock_whatsapp is not None:
            self.mock_whatsapp: bool = mock_whatsapp
        else:
            mock_env = os.getenv("MOCK_WHATSAPP")
            self.mock_whatsapp = mock_env.lower() in ("true", "1") if mock_env is not None else False

        self.alert_callback = alert_callback
        self.metrics: GatewayMetrics = metrics or get_metrics()
        self.last_error: Optional[str] = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=10),
        retry=retry_if_exception_type((
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.HTTPError,
            requests.exceptions.RequestException,
            urllib3.exceptions.HTTPError,
            ConnectionError,
            TimeoutError,
            OSError,
        )),
        reraise=True,
        before_sleep=_log_retry_attempt,
    )
    def _execute_rest(self, api_url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> requests.Response:
        session = get_fork_safe_session()
        response = session.post(api_url, json=payload, headers=headers, timeout=(5.0, 60.0))
        if response.status_code in (408, 429, 502, 503, 504) or response.status_code >= 500:
            response.raise_for_status()
        return response

    def send_message(
        self,
        phone: str,
        message: str,
        media_path: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        force_real: bool = False,
        _is_system_alert: bool = False,
        interactive: bool = False,
    ) -> bool:
        self.last_error = None
        self.metrics.record_dispatch_attempt()
        clean_phone = sanitize_phone(phone)

        if not clean_phone or clean_phone == "+":
            self.last_error = DispatchErrorCause.INVALID_PHONE.value
            self.metrics.record_dispatch_error(self.last_error)
            logger.error("Cannot send WhatsApp message: Sanitized phone is empty (Original: %s).", phone)
            return False

        # Centralized Policy Gatekeeper: Suppresses proactive traffic when disabled.
        # System alerts and interactive user replies are strictly exempt.
        if not self.enable_automated_dispatches and not (_is_system_alert or interactive):
            logger.info("System Gate: Suppressed automated dispatch to %s", clean_phone)
            return True

        resolved_media = resolve_media_path(media_path, self.shared_media_dir)
        message = strip_markdown_media(message, self.shared_media_dir)

        # UX Leak Interceptor: prevents LLM hallucinations of internal system paths or tags
        if not _is_system_alert and detect_ux_leak(message, self.shared_media_dir):
            self.last_error = DispatchErrorCause.UX_LEAK.value
            self.metrics.record_sanitization_redaction()
            self.metrics.record_dispatch_error(self.last_error)
            logger.warning("UX LEAK DETECTED: Message contained system tags or raw paths: %s", message)
            if self.alert_callback:
                try:
                    self.alert_callback(f"UX LEAK DETECTED: LLM hallucinated system data.\nPayload: {message}")
                except Exception:
                    pass
            return False

        # Local Blast Shield / Mock Interceptor
        if self.mock_whatsapp and not force_real:
            logger.info(
                "[MOCK WHATSAPP] Intercepted payload for %s: %s%s",
                clean_phone,
                message,
                f" [MEDIA: {resolved_media}]" if resolved_media else "",
            )
            return True

        if not self.token:
            self.last_error = DispatchErrorCause.MISSING_TOKEN.value
            self.metrics.record_dispatch_error(self.last_error)
            logger.error("Missing OPENCLAW_TOKEN for REST authentication.")
            return False

        api_url = f"{self.gateway_url}/tools/invoke"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        if not idempotency_key:
            idempotency_key = compute_idempotency_key(clean_phone, message, media_path=resolved_media)

        args_dict: Dict[str, Any] = {
            "channel": "whatsapp",
            "account": "default",
            "target": clean_phone,
            "message": message,
            "gatewayToken": self.token,
            "idempotencyKey": idempotency_key,
        }
        if resolved_media:
            args_dict["media"] = resolved_media

        payload: Dict[str, Any] = {
            "tool": "message",
            "action": "send",
            "args": args_dict,
        }

        try:
            response = self._execute_rest(api_url, headers, payload)
            if not response.ok:
                response.raise_for_status()

            try:
                response_data = response.json()
            except (ValueError, json.JSONDecodeError):
                self.last_error = DispatchErrorCause.INVALID_JSON.value
                self.metrics.record_dispatch_error(self.last_error)
                logger.error(
                    "Gateway returned non-JSON to %s: content-type=%s body=%.500r",
                    clean_phone,
                    response.headers.get("Content-Type") if response is not None else "?",
                    response.text if response is not None else "",
                )
                return False

            if not isinstance(response_data, dict):
                self.last_error = DispatchErrorCause.INVALID_JSON.value
                self.metrics.record_dispatch_error(self.last_error)
                logger.error(
                    "Gateway rejected dispatch to %s: non-dict JSON body: %.500r",
                    clean_phone, response_data,
                )
                return False

            # Strict identity check is intentional: a truthy check would accept "false" strings
            if response_data.get("ok") is not True:
                self.last_error = DispatchErrorCause.GATEWAY_REJECTED.value
                self.metrics.record_dispatch_error(self.last_error)
                logger.error(
                    "Gateway rejected dispatch to %s: HTTP %s but ok!=True. Body: %.500r",
                    clean_phone, response.status_code, response_data,
                )
                return False

            return True

        except requests.exceptions.HTTPError as e:
            err_text = e.response.text if e.response is not None else str(e)
            status_code = e.response.status_code if e.response is not None else 0

            if status_code >= 500 and "No active WhatsApp Web listener" in err_text:
                self.last_error = DispatchErrorCause.SESSION_OFFLINE.value
                self.metrics.record_dispatch_error(self.last_error)
                logger.critical(
                    "WhatsApp Channel Offline: No active session. "
                    "Run openclaw channels login to re-link."
                )
                return False

            logger.error("REST API Error: %s - Response: %s", e, err_text)
            if status_code in (401, 403, 404):
                self.last_error = DispatchErrorCause.HTTP_AUTH.value
            else:
                self.last_error = DispatchErrorCause.HTTP_SERVER.value
            self.metrics.record_dispatch_error(self.last_error)
            return False

        except requests.exceptions.Timeout:
            self.last_error = DispatchErrorCause.TIMEOUT.value
            self.metrics.record_dispatch_error(self.last_error)
            logger.error("REST Bridge Timeout")
            return False
        except requests.exceptions.SSLError:
            self.last_error = DispatchErrorCause.SSL_ERROR.value
            self.metrics.record_dispatch_error(self.last_error)
            logger.error("REST Bridge SSL Error")
            return False
        except requests.exceptions.ConnectionError:
            self.last_error = DispatchErrorCause.CONN_ERROR.value
            self.metrics.record_dispatch_error(self.last_error)
            logger.error("REST Bridge Connection Error")
            return False
        except requests.exceptions.RequestException:
            self.last_error = DispatchErrorCause.REQUEST_ERROR.value
            self.metrics.record_dispatch_error(self.last_error)
            logger.error("REST Bridge Request Exception")
            return False
        except Exception as e:
            self.last_error = DispatchErrorCause.UNKNOWN_ERROR.value
            self.metrics.record_dispatch_error(self.last_error)
            logger.error("REST Bridge Unexpected Error: %s", e)
            return False


# Backward compatibility alias
WhatsAppService = WhatsAppGatewayClient
