"""Product command-surface contracts (2026-10-23).

The surface the user sees is now built from three data modules:

* ``command_menu.py``    — the 20 advertised commands (BotFather menu)
* ``command_catalog.py`` — the full public catalog rendered by /more
* ``command_lookup.py``  — slug resolution over the catalog

These tests lock the four properties that make the cut safe:

1. the advertised menu never exceeds 20 and never repeats a row;
2. every advertised command has a real handler, and every catalog command
   too (so /more cannot advertise a dead command);
3. every ``/cmd`` mentioned in /help is registered (no hand-written drift);
4. /desk, /plans and /more render from a fixture store without network or
   Telegram, and /desk degrades gracefully on an empty store.
"""

from __future__ import annotations

import json
import re

import pytest

from rugmunchbot.command_lookup import catalog_commands, resolve_group
from rugmunchbot.command_menu import (
    ADVERTISED_COMMANDS,
    ADVERTISED_SECTIONS,
    MAX_ADVERTISED,
    advertised_set,
    menu_rows,
)
from rugmunchbot.handlers._help_text import HELP_TEXT, MENU_TEXT

FIXTURE_POSTS = [
    {
        "slug": "post-1",
        "ts": "2026-10-22T14:05:00Z",
        "kind": "exploit",
        "source": "",
        "text": "<b>Exploit: $JUP router drained 1.2M</b>\nApproval phishing drained 1.2M.",
    },
    {
        "slug": "post-2",
        "ts": "2026-10-22T16:30:00Z",
        "kind": "midday_market",
        "source": "https://t.me/cryptorugmuncher/901",
        "text": "<b>\u2501\u2501\u2501 Midday Market Report \u2501\u2501\u2501</b>\n\n"
        "The tape is silent: no Fed speakers, no CPI, no equity move.",
    },
]


@pytest.fixture(scope="module")
def registered() -> set[str]:
    """Every command name the real wiring registers (no polling, no network)."""
    from telegram.ext import Application

    from rugmunchbot.bot._commands import (
        collect_registered_commands,
        register_surface_commands,
    )
    from rugmunchbot.bot._entry import _register_all_handlers

    app = Application.builder().token("123456:TEST-TOKEN").build()
    _register_all_handlers(app)
    register_surface_commands(app)
    return collect_registered_commands(app)


@pytest.fixture
def store(tmp_path):
    """A fixture feed store in the exact production shape (top-level array)."""
    path = tmp_path / "posts.json"
    path.write_text(json.dumps(FIXTURE_POSTS))
    return str(path)


# ── 1. menu size + uniqueness ────────────────────────────────────────────


def test_advertised_menu_is_at_most_twenty():
    assert MAX_ADVERTISED == 20
    assert len(advertised_set()) <= MAX_ADVERTISED
    assert len(ADVERTISED_COMMANDS) <= MAX_ADVERTISED


def test_advertised_menu_has_no_duplicate_rows():
    rows = [command for command, _desc in menu_rows()]
    assert len(rows) == len(set(rows)), f"duplicate menu rows: {rows}"
    assert len(ADVERTISED_SECTIONS) == 5


def test_help_reports_the_advertised_count():
    """The /help header number is derived, never hand-typed."""
    assert f"{len(ADVERTISED_COMMANDS)} commands" in HELP_TEXT


# ── 2. registry parity ───────────────────────────────────────────────────


def test_every_advertised_command_is_registered(registered):
    missing = sorted(advertised_set() - registered)
    assert not missing, f"advertised but not registered: {missing}"


def test_every_catalog_command_is_registered(registered):
    """A command in /more must work — otherwise /more lies."""
    missing = sorted(catalog_commands() - registered)
    assert not missing, f"catalog advertises unregistered commands: {missing}"


def test_surface_commands_are_registered(registered):
    assert {"desk", "plans", "more"} <= registered


def test_guard_passes_on_real_wiring(registered):
    """The hard startup guard must pass with the surface registered."""
    from telegram.ext import Application

    from rugmunchbot.bot._commands import register_surface_commands
    from rugmunchbot.bot._entry import _register_all_handlers
    from rugmunchbot.bot._menu_guard import assert_menu_coverage

    app = Application.builder().token("123456:TEST-TOKEN").build()
    _register_all_handlers(app)
    register_surface_commands(app)
    assert_menu_coverage(app)  # must not raise


# ── 3. help/more text parity ─────────────────────────────────────────────


def test_help_mentions_only_registered_commands(registered):
    mentioned = set(re.findall(r"(?<![\w./@<])/([a-z_]{2,30})\b", HELP_TEXT + MENU_TEXT))
    unknown = sorted(mentioned - registered)
    assert not unknown, f"help/more mention unregistered commands: {unknown}"


def test_help_mentions_every_advertised_command():
    for command in advertised_set():
        assert f"/{command}" in HELP_TEXT, f"/help omits advertised /{command}"


# ── 4. /desk, /plans, /more render ───────────────────────────────────────


def test_desk_renders_source_timestamp_and_read_more(store):
    from rugmunchbot.handlers._desk import load_latest_posts, render_desk

    posts = load_latest_posts(limit=2, path=store)
    assert [p["slug"] for p in posts] == ["post-2", "post-1"], "newest first"

    text = render_desk(posts, limit=2)
    assert "Midday Market Report" in text
    assert "2026-10-22 16:30Z" in text, "timestamp missing"
    assert "https://t.me/cryptorugmuncher/901" in text, "source link missing"
    assert "rugmunch.io/blog/post-2" in text, "Read-more deep link missing"
    # The headline must not be pasted into the excerpt slot twice.
    assert text.count("Exploit: $JUP router drained 1.2M") == 1


def test_desk_degrades_on_missing_store(tmp_path):
    from rugmunchbot.handlers._desk import load_latest_posts, render_desk

    assert load_latest_posts(path=str(tmp_path / "absent.json")) == []
    text = render_desk([])
    assert "has not filed yet" in text
    assert "rugmunch.io/feed" in text


def test_plans_states_free_truth_without_a_fake_paywall():
    from rugmunchbot.handlers._plans import plans_text

    text = plans_text()
    assert "<b>Free \u2014 always</b>" in text
    assert "No fake countdown" in text
    # Numbers come from config.TIERS, never retyped in the handler.
    from rugmunchbot.config import TIERS

    assert f"{TIERS['free']['scans_per_week']} scans/week" in text


def test_more_index_and_group_render():
    from rugmunchbot.handlers._more import render_group, render_more

    index = render_more("")
    assert index == MENU_TEXT
    assert "/more scans" in index

    group = render_group("scan")
    assert group is not None, "alias `scan` must resolve"
    assert "/scan" in group
    assert resolve_group("desk") == "desk"
    assert resolve_group("nonexistent-thing") is None


def test_more_unknown_argument_falls_back_to_index():
    from rugmunchbot.handlers._more import render_more

    text = render_more("definitely-not-a-command")
    assert "No group or command called" in text
    assert "<b>The Desk</b>" in text
