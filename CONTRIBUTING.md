# Contributing to whatsapp-ops-gateway

We welcome contributions, bug fixes, and architectural improvements. To guarantee socket safety, strict delivery semantics, and zero IP exposure under production load, all contributions must adhere to our SRE standards.

---

## 🛠️ Development Workflow

No host dependencies are required. All tests and audits run inside containers:

```bash
# Display all available targets
make help

# Build local container image
make build

# Run complete test suite in isolated container
make test

# Run static clean-room leak scan
make check-blacklist

# Run container security & vulnerability scan
make audit

# Clean up dangling containers and volumes
make clean
```

---

## 🛡️ SRE Architecture Rules

1. **Fork Safety:** Sockets and connection pools must never be shared across `os.fork()`. All HTTP session caches must register child cleanup via `os.register_at_fork`.
2. **De-amplification:** HTTP adapters must enforce `max_retries=0`. All retry logic is owned exclusively by application-level Tenacity decorators.
3. **Quantized Idempotency:** Fallback idempotency keys must be deterministic SHA256 hashes quantized into 10-minute buckets. Never generate unstructured random UUIDs for recurring automated dispatches.
4. **Strict Truth Verification:** Gateway HTTP 200 responses must validate `response.get("ok") is True` using identity checks. Never rely on truthy string conversions.
5. **Clean-Room Verification:** All code must pass `./scripts/check_blacklist.sh` prior to commit. Never include proprietary tenant IDs, internal endpoints, or hardcoded secrets.

---

## 👥 Contributors

- **Emmanuel Ortiz** ([@emmanuelol](https://github.com/emmanuelol)) - Core Architecture & SRE
- **Carlos Ortiz** ([@carlosaol](https://github.com/carlosaol)) - Systems Engineering & Concurrency Design

---

## 📄 Pull Request Checklist

- [ ] All unit and integration tests pass cleanly via `make test`.
- [ ] No blacklisted patterns detected via `make check-blacklist`.
- [ ] Container image builds and audits clean via `make audit`.
- [ ] All public methods contain type hints and docstrings.
