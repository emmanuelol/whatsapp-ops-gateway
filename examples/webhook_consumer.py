#!/usr/bin/env python3
"""
Example Webhook Consumer Script

Demonstrates integrating whatsapp-ops-gateway into an alert pipeline:
1. Normalizes and sanitizes destination phone numbers.
2. Derives deterministic idempotency keys for at-least-once webhooks.
3. Records structured observability metrics.
"""

from __future__ import annotations

import argparse
import sys
from whatsapp_ops_gateway.sanitization import sanitize_phone, detect_ux_leak
from whatsapp_ops_gateway.idempotency import compute_idempotency_key
from whatsapp_ops_gateway.telemetry import get_metrics
from whatsapp_ops_gateway.taxonomy import DispatchErrorCause


def process_incoming_alert(phone: str, message: str, dry_run: bool = False) -> int:
    metrics = get_metrics()
    metrics.record_dispatch_attempt()

    # Step 1: Phone Sanitization
    normalized_phone = sanitize_phone(phone)
    if not normalized_phone:
        print(f"[REJECT] Invalid phone number provided: {phone}", file=sys.stderr)
        metrics.record_dispatch_error(DispatchErrorCause.INVALID_PHONE.value)
        return 1

    # Step 2: UX / Path Leak Guard
    if detect_ux_leak(message):
        print("[REJECT] Message contains prohibited local paths or raw UX leak patterns.", file=sys.stderr)
        metrics.record_sanitization_redaction()
        metrics.record_dispatch_error(DispatchErrorCause.UX_LEAK.value)
        return 1

    # Step 3: Compute Idempotency Key
    idempotency_key = compute_idempotency_key(normalized_phone, message)

    print("--- Webhook Alert Processed Successfully ---")
    print(f"Target Phone:     {normalized_phone}")
    print(f"Idempotency Key:  {idempotency_key}")
    print(f"Message Length:   {len(message)} chars")
    print(f"Execution Mode:   {'DRY RUN (simulation)' if dry_run else 'DISPATCH'}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an example ops alert consumer using whatsapp-ops-gateway primitives."
    )
    parser.add_argument(
        "--phone",
        default="+52 (472) 699-1461",
        help="Recipient phone number to sanitize and dispatch to.",
    )
    parser.add_argument(
        "--message",
        default="Alert [CRITICAL]: Host db-primary-01 CPU utilization > 95%",
        help="Alert body payload to inspect and send.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Simulate execution without sending actual network requests (default: True).",
    )
    parser.add_argument(
        "--show-metrics",
        action="store_true",
        help="Display Prometheus metrics exposition after execution.",
    )

    args = parser.parse_args()

    exit_code = process_incoming_alert(args.phone, args.message, dry_run=args.dry_run)

    if args.show_metrics:
        print("\n--- Telemetry Metrics (Prometheus Format) ---")
        print(get_metrics().render_prometheus())

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
