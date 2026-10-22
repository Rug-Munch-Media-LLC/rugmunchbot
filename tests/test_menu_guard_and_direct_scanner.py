"""Menu-drift guard + direct-scanner adapter tests (2026-09-20 cutover prep).

Covers the two batch-1 fixes:

* ``bot/_menu_guard.py`` — the hard startup drift check. The 24 commands
  from the audit must be either advertised in the menu or declared
  admin-hidden; anything else is drift and must raise.
* ``tools/scanner/_scanner_direct.py`` — the feature-flagged adapter stub.
  With ``SCANNER_DIRECT`` unset (default) every entry point returns ``None``
  so callers keep their existing fallback chain (zero behaviour change).
"""

from __future__ import annotations

import pytest

from rugmunchbot.bot._menu_guard import (
    ADMIN_MENU_COMMANDS,
    MenuDriftError,
    assert_menu_coverage,
)
from rugmunchbot.tools.scanner import _scanner_direct as direct

#: The 24 commands the 2026-09-20 audit found missing from the menu.
AUDIT_DRIFT_COMMANDS = frozenset(
    {
        "accuracy",
        "admin",
        "admin_ban",
        "admin_broadcast",
        "admin_logs",
        "admin_set_tier",
        "admin_stats",
        "admin_toggle",
        "admin_toggle_chat",
        "admin_unban",
        "admin_user",
        "backtest",
        "backup",
        "banuser",
        "budget",
        "credits",
        "deploy",
        "logs",
        "menu",
        "restart",
        "services",
        "sources",
        "users",
        "whalewatch",
    }
)


class _FakeApp:
    """Minimal Application stand-in exposing ``handlers`` for the guard."""

    def __init__(self, commands: set[str]) -> None:
        from telegram.ext import CommandHandler

        self.handlers = {0: [CommandHandler(name, _noop) for name in sorted(commands)]}


async def _noop(update, ctx) -> None:  # pragma: no cover - never invoked
    return None


def test_audit_drift_commands_are_all_declared_or_advertised():
    """Every audit command is either in the menu or declared admin-hidden."""
    from rugmunchbot.bot._commands import _MENU_COMMANDS

    unresolved = AUDIT_DRIFT_COMMANDS - _MENU_COMMANDS - ADMIN_MENU_COMMANDS
    assert not unresolved, f"audit commands still unresolved: {sorted(unresolved)}"


def test_admin_menu_commands_are_registered():
    """Every declared admin-hidden command has a real handler registered.

    Builds the real app wiring (no network) and asserts the admin-hidden set
    is a subset of what is actually registered — a declared-but-dead command
    is drift in the other direction.
    """
    from rugmunchbot.bot._commands import collect_registered_commands

    app = _build_real_app()
    registered = collect_registered_commands(app)
    missing = sorted(ADMIN_MENU_COMMANDS - registered)
    assert not missing, f"admin-hidden commands not registered: {missing}"


def test_assert_menu_coverage_passes_on_real_wiring():
    """The real app wiring has zero drift (advertised ⊆ registered, ≤ 20)."""
    app = _build_real_app()
    assert_menu_coverage(app)  # must not raise


def test_assert_menu_coverage_allows_hidden_registered():
    """Registered-but-unadvertised is the DESIGN (progressive disclosure).

    ``totally_new_command`` is reachable — it is simply not in the BotFather
    menu — so the guard must not fail the boot over it.
    """
    from rugmunchbot.command_menu import advertised_set

    app = _FakeApp(set(advertised_set()) | {"totally_new_command"})
    assert_menu_coverage(app)  # must not raise


def test_assert_menu_coverage_raises_on_phantom():
    """A menu entry with no handler is drift (broken menu)."""
    app = _FakeApp(set())
    with pytest.raises(MenuDriftError, match="advertised but unregistered"):
        assert_menu_coverage(app)


def _build_real_app():
    """Build the real Application wiring without starting polling.

    Mirrors ``bot/_entry.main`` registration but skips the network-bound
    ``Application.builder().token(...)`` — a bare ``Application`` is enough
    for handler collection.
    """
    from telegram.ext import Application

    from rugmunchbot.bot._commands import register_surface_commands
    from rugmunchbot.bot._entry import _register_all_handlers

    app = Application.builder().token("123456:TEST-TOKEN").build()
    _register_all_handlers(app)
    # Startup parity: setup_bot_profile registers the menu-owned surface
    # (/desk, /plans, /more) before either guard runs.
    register_surface_commands(app)
    return app


# ── direct-scanner adapter stub ─────────────────────────────────────────


def test_scanner_direct_disabled_by_default(monkeypatch):
    """Default (flag unset) → adapter inert."""
    monkeypatch.delenv(direct.SCANNER_DIRECT_ENV, raising=False)
    assert direct.scanner_direct_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_scanner_direct_enabled_truthy(monkeypatch, value):
    monkeypatch.setenv(direct.SCANNER_DIRECT_ENV, value)
    assert direct.scanner_direct_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_scanner_direct_disabled_falsy(monkeypatch, value):
    monkeypatch.setenv(direct.SCANNER_DIRECT_ENV, value)
    assert direct.scanner_direct_enabled() is False


async def test_direct_scan_token_returns_none_when_disabled(monkeypatch):
    monkeypatch.delenv(direct.SCANNER_DIRECT_ENV, raising=False)
    assert await direct.direct_scan_token("0xabc", "ethereum") is None


async def test_direct_scan_token_returns_none_when_url_unset(monkeypatch):
    """Flag on but no service URL → still inert (no behaviour change)."""
    monkeypatch.setenv(direct.SCANNER_DIRECT_ENV, "1")
    monkeypatch.delenv(direct.SCANNER_DIRECT_URL_ENV, raising=False)
    assert await direct.direct_scan_token("0xabc", "ethereum", deep=True) is None


async def test_direct_scan_wallet_returns_none_when_disabled(monkeypatch):
    monkeypatch.delenv(direct.SCANNER_DIRECT_ENV, raising=False)
    assert await direct.direct_scan_wallet("0xabc", "ethereum") is None
