"""Shared fixtures for adapter tests — patches rate limiting to avoid real sleeps."""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture(autouse=True)
def disable_rate_limiting():
    """Patch _rate_limit to be a no-op in all adapter tests."""
    with patch(
        "sportshub.ingestion.base.SourceAdapter._rate_limit",
        new_callable=AsyncMock,
    ):
        yield
