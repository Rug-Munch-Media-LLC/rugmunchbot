"""Pytest configuration for rugmunchbot tests."""

import os
import sys

import pytest

# Ensure src is on path for all tests
sys.path.insert(0, "/srv/work/repos/rugmunchbot/src")
sys.path.insert(0, "/srv/work/repos/rugmunchbot")
sys.path.insert(0, "/srv/work/repos/rugmunch-scanner")

# Load env for tests
from dotenv import load_dotenv

load_dotenv("/srv/secrets/rugmunchbot.env")

# Tests must never publish. This file loads the REAL production env, which
# carries a live GHOST_ADMIN_API_KEY, and the autouse fixture below disables
# the dedupe layer — so a test that reaches ghost_mirror_post() writes a real
# post to the production Ghost database. That is how 429 fixture rows
# ("first", "second", "Hello world", "Whale wallet 0xabc") got into a live
# content store, and the count is still growing: the same slug appeared as
# post-1790711756, post-1790711756-2 and post-1790711756-3 in one run.
#
# Strip the publishing credentials from the test process. A test that needs
# to exercise the Ghost client should inject its own key via a fixture
# pointing at a test database — never at production.
for _var in (
    "GHOST_ADMIN_API_KEY",
    "GHOST_ADMIN_KEY",
    "GHOST_CONTENT_API_KEY",
):
    os.environ.pop(_var, None)


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
