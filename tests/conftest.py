"""Pytest configuration for rugmunchbot tests."""

import sys

import pytest

# Ensure src is on path for all tests
sys.path.insert(0, "/srv/work/repos/rugmunchbot/src")
sys.path.insert(0, "/srv/work/repos/rugmunchbot")
sys.path.insert(0, "/srv/work/repos/rugmunch-scanner")

# Load env for tests
from dotenv import load_dotenv

load_dotenv("/srv/secrets/rugmunchbot.env")


@pytest.fixture(autouse=True)
def _disable_durable_dedupe(monkeypatch):
    """Disable the durable Redis dedupe layer for EVERY test.

    ``_remember``'s durable backstop reads the PRODUCTION
    ``bot:channels:posted:__global__`` set. Test fixtures that reach a
    successful send persist their keys there (``spawn_persist``), and every
    later test run then black-holes those same fixtures ("ref-1" is forever
    "already posted"). Tests that exercise durability mock the layer
    themselves (test_scheduler_dedup_durable) — their monkeypatches override
    this one.
    """
    from rugmunchbot.channels import scheduler_state

    monkeypatch.setattr(scheduler_state, "is_available", lambda: False)
