import os
import logging
from typing import Optional, Callable, Any
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
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

logger = logging.getLogger(__name__)


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
    """

    def __init__(
        self,
        token: Optional[str] = None,
        gateway_url: Optional[str] = None,
        shared_media_dir: Optional[str] = None,
        enable_automated_dispatches: Optional[bool] = None,
        mock_whatsapp: Optional[bool] = None,
        alert_callback: Optional[Callable[[str], Any]] = None,
    ):
        self.token = (
            token
            if token is not None
            else os.getenv("OPENCLAW_TOKEN", "")
        ).strip().strip("'\"")

        bot_host = os.getenv("OPENCLAW_BOT_CONTAINER", "openclaw_bot")
        default_gateway = os.getenv("OPENCLAW_GATEWAY_URL", f"http://{bot_host}:18789")
        self.gateway_url = (gateway_url or default_gateway).rstrip("/")

        self.shared_media_dir = (shared_media_dir or os.getenv("SHARED_MEDIA_DIR", "/shared_media")).rstrip("/")

        if enable_automated_dispatches is not None:
            self.enable_automated_dispatches = enable_automated_dispatches
        else:
            env_val = os.getenv("ENABLE_AUTOMATED_DISPATCHES", "true").lower()
            self.enable_automated_dispatches = env_val in ("1", "true", "yes")

        if mock_whatsapp is not None:
            self.mock_whatsapp = mock_whatsapp
        else:
            mock_env = os.getenv("MOCK_WHATSAPP")
            self.mock_whatsapp = mock_env.lower() in ("true", "1") if mock_env is not None else False

        self.alert_callback = alert_callback
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
        before_sleep=lambda rs: logger.warning(
            f"OpenClaw unreachable, retrying in {rs.next_action.sleep} seconds..."
        ),
    )
    def _execute_rest(self, api_url: str, headers: dict, payload: dict) -> requests.Response:
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
        clean_phone = sanitize_phone(phone)

        if not clean_phone or clean_phone == "+":
            self.last_error = DispatchErrorCause.INVALID_PHONE.value
            logger.error(f"Cannot send WhatsApp message: Sanitized phone is empty (Original: {phone}).")
            return False

        # Centralized Policy Gatekeeper: Suppresses proactive traffic when disabled.
        # System alerts and interactive user replies are strictly exempt.
        if not self.enable_automated_dispatches and not (_is_system_alert or interactive):
            logger.info(f"System Gate: Suppressed automated dispatch to {clean_phone}")
            return True

        resolved_media = resolve_media_path(media_path, self.shared_media_dir)
        message = strip_markdown_media(message, self.shared_media_dir)

        # UX Leak Interceptor: prevents LLM hallucinations of internal system paths or tags
        if not _is_system_alert and detect_ux_leak(message, self.shared_media_dir):
            self.last_error = DispatchErrorCause.UX_LEAK.value
            logger.warning(f"UX LEAK DETECTED: Message contained system tags or raw paths: {message}")
            if self.alert_callback:
                try:
                    self.alert_callback(f"UX LEAK DETECTED: LLM hallucinated system data.\nPayload: {message}")
                except Exception:
                    pass
            return False

        # Local Blast Shield / Mock Interceptor
        if self.mock_whatsapp and not force_real:
            logger.info(
                f"[MOCK WHATSAPP] Intercepted payload for {clean_phone}: {message} "
                + (f"[MEDIA: {resolved_media}]" if resolved_media else "")
            )
            return True

        if not self.token:
            self.last_error = DispatchErrorCause.MISSING_TOKEN.value
            logger.error("Missing OPENCLAW_TOKEN for REST authentication.")
            return False

        api_url = f"{self.gateway_url}/tools/invoke"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        if not idempotency_key:
            idempotency_key = compute_idempotency_key(clean_phone, message, media_path=resolved_media)

        payload = {
            "tool": "message",
            "action": "send",
            "args": {
                "channel": "whatsapp",
                "account": "default",
                "target": clean_phone,
                "message": message,
                "gatewayToken": self.token,
                "idempotencyKey": idempotency_key,
            },
        }
        if resolved_media:
            payload["args"]["media"] = resolved_media

        try:
            response = self._execute_rest(api_url, headers, payload)
            if not response.ok:
                response.raise_for_status()

            try:
                response_data = response.json()
            except (ValueError, getattr(requests.exceptions, "JSONDecodeError", ())):
                self.last_error = DispatchErrorCause.INVALID_JSON.value
                logger.error(
                    "Gateway returned non-JSON to %s: content-type=%s body=%.500r",
                    clean_phone,
                    response.headers.get("Content-Type") if response is not None else "?",
                    response.text if response is not None else "",
                )
                return False

            if not isinstance(response_data, dict):
                self.last_error = DispatchErrorCause.INVALID_JSON.value
                logger.error(
                    "Gateway rejected dispatch to %s: non-dict JSON body: %.500r",
                    clean_phone, response_data,
                )
                return False

            # Strict identity check is intentional: a truthy check would accept "false" strings
            if response_data.get("ok") is not True:
                self.last_error = DispatchErrorCause.GATEWAY_REJECTED.value
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
                logger.critical(
                    "WhatsApp Channel Offline: No active session. "
                    "Run openclaw channels login to re-link."
                )
                return False

            logger.error(f"REST API Error: {e} - Response: {err_text}")
            if status_code in (401, 403, 404):
                self.last_error = DispatchErrorCause.HTTP_AUTH.value
            else:
                self.last_error = DispatchErrorCause.HTTP_SERVER.value
            return False

        except requests.exceptions.Timeout:
            self.last_error = DispatchErrorCause.TIMEOUT.value
            logger.error("REST Bridge Timeout")
            return False
        except requests.exceptions.SSLError:
            self.last_error = DispatchErrorCause.SSL_ERROR.value
            logger.error("REST Bridge SSL Error")
            return False
        except requests.exceptions.ConnectionError:
            self.last_error = DispatchErrorCause.CONN_ERROR.value
            logger.error("REST Bridge Connection Error")
            return False
        except requests.exceptions.RequestException:
            self.last_error = DispatchErrorCause.REQUEST_ERROR.value
            logger.error("REST Bridge Request Exception")
            return False
        except Exception as e:
            self.last_error = DispatchErrorCause.UNKNOWN_ERROR.value
            logger.error(f"REST Bridge Unexpected Error: {e}")
            return False


# Backward compatibility alias
WhatsAppService = WhatsAppGatewayClient
