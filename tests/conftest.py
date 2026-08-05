"""pytest configuration for rugmunchbot tests.

Provides:
- isolated temp SQLite DB (BOT_DB_PATH) for all handler tests
- a clean_db fixture that resets the shared test DB per test
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Isolated temp DB — set before any module imports db so the whole session
# shares one throwaway database (never touches /var/data).
_db_fd, _db_path = tempfile.mkstemp(prefix="rugmunchbot_test_", suffix=".db")
os.environ["BOT_DB_PATH"] = _db_path

# Make the bot package importable (top-level handlers/services/config/db).
_BOT_DIR = str(Path(__file__).resolve().parent.parent / "bot" / "app" / "telegram_bot")
sys.path.insert(0, _BOT_DIR)

_DB_TABLES = (
    "users",
    "weekly_usage",
    "watchlists",
    "scan_history",
    "star_transactions",
    "alert_subscriptions",
    "alert_sent_log",
)


def _clear_db():
    import db

    conn = db.get_db()
    for table in _DB_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: cross-service integration tests (require external services running)",
    )


@pytest.fixture
def clean_db():
    """Reset the shared test DB before and after each test."""
    _clear_db()
    yield
    _clear_db()


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """Clean up the temp DB file after the session."""
    os.close(_db_fd)
    if os.path.exists(_db_path):
        os.unlink(_db_path)
