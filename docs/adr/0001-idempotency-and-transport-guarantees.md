# ADR 0001: Deterministic Idempotency Keys and Zero Connection-Layer Retries

- **Status:** Accepted
- **Deciders:** Operations & Gateway Architecture Team
- **Date:** 2026-10-22

## Context & Problem Statement

Operations alert gateways process webhooks from monitoring systems (Alertmanager, Grafana, Datadog) that deliver notifications under **at-least-once** delivery semantics. If a network blip occurs during delivery acknowledgment, the webhook sender re-dispatches the identical alert.

Furthermore, naive HTTP client usage introduces connection pool retry loops. If an upstream WhatsApp bridge stalls or returns HTTP 503, connection-level retries multiply traffic, turning a minor degradation into a sustained outage.

## Decision Drivers

1. **Zero Duplicate Alert Flooding:** Critical on-call engineers must never receive duplicate WhatsApp notifications for the same alert event within a standard incident triage window.
2. **Preventing Retry Cascades:** The gateway must not amplify transport-level requests against upstream services during degraded conditions.
3. **Decoupled Backend Agnosticism:** Downstream consumers should not need an external Redis cluster just to calculate duplicate fingerprints.
4. **Sub-Millisecond Overhead:** Primitives must add low single-digit microsecond overhead to hot paths.

## Considered Options

1. **Rely on Upstream Deduplication:** Defer idempotency to the external WhatsApp API.
   * *Rejected:* Most upstream APIs lack deterministic deduplication across connection resets.
2. **Standard HTTP Connection Retries (urllib3 default):**
   * *Rejected:* Multiplies load against failing servers ($3 \times 3$ retry storms).
3. **External Cache / Redis Distributed Lock:**
   * *Rejected:* Adds 1–5ms network RTT and introduces an external failure domain for simple alert hashing.
4. **Deterministic Local Hashing + Zero Transport Retries (Adopted):**
   * Calculate SHA-256 fingerprint over phone, sanitized body, media reference, and 10-minute floor epoch.
   * Enforce `max_retries=0` in HTTP adapters.
   * Orchestrate explicit retries only on safe status codes via jittered backoff.

## Decision Outcome

Adopted Option 4.

### Implementation Specifics
- `idempotency.py` provides `compute_idempotency_key(phone, message, media_path=None, now=None)` producing a 64-character SHA-256 hex digest.
- `session.py` initializes an `HTTPAdapter` with `max_retries=0` attached to the thread-safe session singleton.
- `client.py` passes the idempotency key in HTTP headers (`Idempotency-Key`) and tags telemetry counters on duplicates.

## Empirical Benchmark Validation

Measured on Linux CPython runtime via `benchmarks/benchmark_primitives.py` (10,000 iterations):

| Operation | Throughput | Median (p50) | 95th Percentile | 99th Percentile | Mean Overhead |
|---|---|---|---|---|---|
| `sanitize_phone` | **227,990 ops/s** | 2.37 µs | 3.56 µs | 4.75 µs | 2.93 µs |
| `compute_idempotency_key` | **213,231 ops/s** | 3.56 µs | 4.75 µs | 4.75 µs | 3.21 µs |
| `detect_ux_leak` | **267,268 ops/s** | 2.37 µs | 2.38 µs | 2.38 µs | 2.29 µs |
| `metrics.record_attempt` | **333,376 ops/s** | 1.19 µs | 2.37 µs | 2.38 µs | 1.55 µs |

Computing idempotency keys in-memory takes **3.56 µs**, compared to ~2,000 µs (2 ms) for an external Redis call—achieving a **560x latency improvement** while retaining 100% determinism.

## Consequences

### Positive
- Webhook consumer crashes or retries do not trigger duplicate phone pings within the 10-minute window.
- Gateway traffic remains predictable and bounded during upstream outages.
- Unit tests can run deterministically offline without running external cache containers.
- Zero external infrastructure dependency for deduplication.

### Negative / Trade-offs
- An identical message intended to be re-sent within the same 10-minute window requires explicit entropy or prefix differentiation (provided via `compute_interactive_key`).
