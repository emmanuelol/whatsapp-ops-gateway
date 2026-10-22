# 📱 whatsapp-ops-gateway

[![CI Pipeline](https://github.com/emmanuelol/whatsapp-ops-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/emmanuelol/whatsapp-ops-gateway/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

SRE-hardened Python client for WhatsApp message dispatch via OpenClaw REST gateway. Designed specifically for high-concurrency background job workers (e.g. Celery, RQ), autonomous LLM agents, and mission-critical conversational operations.

---

## 🎯 Key Architectural Primitives

| Primitive | Mechanism | SRE Guarantee |
|---|---|---|
| **Process Fork Isolation** | `os.register_at_fork(after_in_child=...)` | Eliminates parent HTTP connection pool inheritance across worker child processes. Zero `BadStatusLine` socket corruption. |
| **Quantized Idempotency** | 10-minute SHA256 time bucket | Prevents gateway message duplication on network retries while allowing legitimate recurring notifications. |
| **Interactive Entropy Keys** | Per-interaction UUID nonces | Bypasses daily duplicate locks for user-initiated conversational sessions. |
| **Centralized Kill-Switch** | `ENABLE_AUTOMATED_DISPATCHES` | Single-point proactive message suppression with automatic exemptions for critical SRE alerts and interactive replies. |
| **Retry De-amplification** | Transport `max_retries=0`, Tenacity exponential backoff | Prevents catastrophic $3 \times 3$ multiplicative request explosion against the gateway. |
| **Strict Identity Verification** | Strict `response.get("ok") is True` | Rejects truthy strings, non-dict payloads, and truncated gateway errors without silent delivery failures. |
| **Error Taxonomy** | 13-cause `DispatchErrorCause` enum | Unambiguous error classification (`session_offline`, `ux_leak`, `gateway_rejected`, etc.). |
| **Keepalive Watchdog** | Jittered 12–25m WebSocket self-ping & bridge monitor | Prevents Meta 30-minute idle disconnects and detects container network partitions. |

---

## 🚀 Quickstart

```python
from whatsapp_ops_gateway import WhatsAppGatewayClient

# Initialize client (picks up OPENCLAW_TOKEN, OPENCLAW_GATEWAY_URL from environment)
client = WhatsAppGatewayClient()

# 1. Proactive Notification (subject to automated dispatch policy)
success = client.send_message(
    phone="+5215512345678",
    message="Your daily operational summary is ready.",
)

# 2. Critical SRE Alert (always bypasses kill-switch)
client.send_message(
    phone="+5215512345678",
    message="🚨 Database replica lag > 15 seconds.",
    _is_system_alert=True,
)

# 3. Interactive Conversational Reply (bypasses kill-switch)
client.send_message(
    phone="+5215512345678",
    message="Here is the invoice breakdown you requested.",
    interactive=True,
)
```

---

## 🔍 Gateway Keepalive Watchdog

```python
from whatsapp_ops_gateway.keepalive import GatewayKeepalive

keepalive = GatewayKeepalive(
    state_file="/tmp/keepalive_state.json",
    bridge_url="http://127.0.0.1:8080/health",
    bot_phone="+5215512345678",
)

# Call periodically via cron or task scheduler
keepalive.ping()
```

---

## 🧪 Testing & Verification

```bash
# Run isolated test suite in Docker
make test

# Security leak & pattern audit
make audit
```

## 📄 License
MIT © 2026
