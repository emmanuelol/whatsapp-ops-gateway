# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a Vulnerability

We take the security of `whatsapp-ops-gateway` and downstream messaging infrastructure seriously. If you discover an operational vulnerability, secret leak vector, or denial-of-service condition:

1. **Do not open a public issue.**
2. Send an email to `security@opsgateway.org` (or report via private GitHub Security Advisory).
3. Include detailed steps to reproduce, impact assessment, and affected versions.
4. We acknowledge receipt within 48 hours and coordinate fix timelines before public disclosure.

---

## Threat Model & Invariant Guardrails

As a critical operations gateway, this package enforces strict defense-in-depth boundaries:

### 1. Transport & Amplification Defense
- **Zero Retries at Connection Layer:** `session.py` strictly configures `max_retries=0` on `urllib3.HTTPAdapter`. Transport retries are orchestrated explicitly at the application boundary via `tenacity` with exponential jitter, preventing cascading retry storms against upstream WhatsApp servers.
- **Fork Safety:** Process isolation with `os.register_at_fork` resets HTTP connection pools upon child process creation (e.g., in Gunicorn/Celery prefork models), preventing cross-process socket pollution.

### 2. Payload & Input Sanitization
- **Phone Number Canonicalization:** `sanitize_phone()` strips arbitrary whitespace and punctuation, strictly enforces canonical E.164-compatible patterns, and rejects malformed inputs before network dispatch.
- **Path Traversal Mitigation:** `resolve_media_path()` confines local media file access strictly within designated shared directories (`SHARED_MEDIA_DIR`), blocking directory traversal attempts (e.g., `../../etc/passwd`).
- **UX & Internal Leak Protection:** `detect_ux_leak()` and `strip_markdown_media()` proactively detect and intercept raw local paths, internal IP patterns, and dangling media placeholders before messages reach end-user devices.

### 3. Supply Chain & Clean-Room Verification
- **Automated Blacklist Checks:** `scripts/check_blacklist.sh` runs in pre-commit and CI to fail closed if private IP ranges, internal tokens, or unauthorized test tooling leak into version control.
- **Container Audits:** Docker containers run non-root and pass automated file-leak and Trivy vulnerability scans prior to deployment.
