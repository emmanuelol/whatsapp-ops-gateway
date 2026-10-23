from collections.abc import Generator

import pytest

from whatsapp_ops_gateway.telemetry import GatewayMetrics, get_metrics


@pytest.fixture(autouse=True)
def clean_metrics() -> Generator[GatewayMetrics, None, None]:
    """Reset telemetry counters before each test execution."""
    metrics = get_metrics()
    metrics.reset()
    yield metrics
    metrics.reset()


@pytest.fixture
def mock_gateway_url() -> str:
    """Canonical test gateway URL."""
    return "http://localhost:8080"


@pytest.fixture
def sample_phone_numbers() -> dict[str, str]:
    """Standard fixtures for phone number normalization."""
    return {
        "10_digits": "4726991461",
        "mx_with_prefix": "+524726991461",
        "mx_canonical": "+5214726991461",
        "mx_spaced": "+521 472 699 1461",
        "invalid_short": "12345",
    }


@pytest.fixture
def sample_payload() -> dict[str, str]:
    """Standard dispatch payload."""
    return {
        "phone": "+5214726991461",
        "message": "Production alert: disk space threshold exceeded.",
    }


@pytest.fixture
def isolated_media_dir(tmp_path: pytest.TempPathFactory) -> str:
    """Dedicated temporary media directory for sandboxed path resolution tests."""
    return str(tmp_path)
