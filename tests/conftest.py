"""pytest configuration for rugmunchbot tests."""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: cross-service integration tests (require external services running)",
    )
