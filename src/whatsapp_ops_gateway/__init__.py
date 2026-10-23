"""
whatsapp-ops-gateway: SRE-hardened WhatsApp gateway client with fork isolation,
deterministic idempotency, and automated dispatch gatekeeping.
"""

__version__ = "0.1.0"

from whatsapp_ops_gateway.idempotency import (
    compute_idempotency_key,
    compute_interactive_key,
)
from whatsapp_ops_gateway.sanitization import (
    detect_ux_leak,
    resolve_media_path,
    sanitize_phone,
    strip_markdown_media,
)
from whatsapp_ops_gateway.session import get_fork_safe_session
from whatsapp_ops_gateway.taxonomy import DispatchErrorCause
from whatsapp_ops_gateway.telemetry import (
    GatewayMetrics,
    JsonLogFormatter,
    get_metrics,
)

__all__ = [
    "__version__",
    "DispatchErrorCause",
    "get_fork_safe_session",
    "compute_idempotency_key",
    "compute_interactive_key",
    "sanitize_phone",
    "resolve_media_path",
    "strip_markdown_media",
    "detect_ux_leak",
    "GatewayMetrics",
    "JsonLogFormatter",
    "get_metrics",
]
