# Multi-stage Dockerfile leveraging uv for reproducible, deterministic builds
FROM ghcr.io/astral-sh/uv:latest AS uv_bin

FROM python:3.11-slim AS builder
WORKDIR /app

COPY --from=uv_bin /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# Copy metadata and lockfile first for optimal Docker layer caching
COPY pyproject.toml uv.lock README.md ./

# Sync dependencies defined in uv.lock into /opt/venv
RUN uv sync --frozen --all-extras --no-install-project

# Copy application source and build wheel
COPY src/ ./src/
RUN uv sync --frozen --all-extras

FROM python:3.11-slim AS runtime
WORKDIR /app

# Harden base runtime: patch OS CVEs and purge unpinned base packaging tools
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* && \
    rm -rf /usr/local/lib/python3.11/site-packages/pip* \
           /usr/local/lib/python3.11/site-packages/setuptools* \
           /usr/local/lib/python3.11/site-packages/wheel* \
           /usr/local/lib/python3.11/ensurepip

# Copy isolated virtual environment from builder to standard system path
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/src"

COPY README.md pyproject.toml ./
COPY src/ ./src/
COPY tests/ ./tests/

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["pytest"]
CMD ["tests/", "-v", "--tb=short"]
