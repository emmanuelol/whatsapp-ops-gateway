from enum import Enum


class DispatchErrorCause(str, Enum):
    """Structured error taxonomy for WhatsApp dispatch failures."""
    INVALID_PHONE = "invalid_phone"
    UX_LEAK = "ux_leak"
    MISSING_TOKEN = "missing_token"
    SESSION_OFFLINE = "session_offline"
    HTTP_AUTH = "http_auth"
    HTTP_SERVER = "http_server"
    INVALID_JSON = "invalid_json"
    TIMEOUT = "timeout"
    SSL_ERROR = "ssl_error"
    CONN_ERROR = "conn_error"
    REQUEST_ERROR = "request_error"
    GATEWAY_REJECTED = "gateway_rejected"
    UNKNOWN_ERROR = "unknown_error"
