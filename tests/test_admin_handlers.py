"""Tests for the @rmi_admin_bot server-management command handlers.

Covers the owner-gating, subprocess execution helper, and the graceful
failure contract of ``handlers.admin``. The subprocess commands themselves
are mocked so the tests run anywhere (no Docker / systemd required).
"""

from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rugmunchbot.handlers.admin import (
    _run,
    cmd_alerts,
    cmd_backup,
    cmd_budget,
    cmd_deploy,
    cmd_help,
    cmd_logs,
    cmd_restart,
    cmd_services,
    cmd_sources,
    cmd_status,
)


def _make_update(owner: bool = True) -> MagicMock:
    """Build a minimal Update stub with an effective_user + message."""
    update = MagicMock()
    user = MagicMock()
    user.id = 111 if owner else 999
    user.username = "owner" if owner else "intruder"
    update.effective_user = user
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    update.effective_message = msg
    return update


def _make_ctx(args: list[str] | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args
    return ctx


# ── _run helper ────────────────────────────────────────────────────────────


def test_run_success() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        rc, out = _run(["echo", "hi"])
    assert rc == 0
    assert out == "ok"


def test_run_timeout() -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
        rc, out = _run(["sleep", "99"])
    assert rc == 124
    assert "timed out" in out.lower()


def test_run_missing_binary() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError("nope")):
        rc, out = _run(["definitely-not-a-real-binary"])
    assert rc == 127
    assert "not found" in out.lower()


def test_run_generic_exception() -> None:
    with patch("subprocess.run", side_effect=RuntimeError("boom")):
        rc, out = _run(["whatever"])
    assert rc == 1
    assert "failed" in out.lower()


# ── owner gating ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "handler",
    [
        cmd_status,
        cmd_services,
        cmd_restart,
        cmd_logs,
        cmd_deploy,
        cmd_backup,
        cmd_alerts,
        cmd_budget,
        cmd_sources,
        cmd_help,
    ],
)
async def test_non_owner_denied(handler) -> None:
    update = _make_update(owner=False)
    ctx = _make_ctx()
    with patch("rugmunchbot.handlers.admin.is_owner", return_value=False):
        await handler(update, ctx)
    update.effective_message.reply_text.assert_awaited_once()
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Owner only" in text


# ── /services ──────────────────────────────────────────────────────────────


async def test_services_lists_rmi_services() -> None:
    update = _make_update(owner=True)
    ctx = _make_ctx()
    with (
        patch("rugmunchbot.handlers.admin.is_owner", return_value=True),
        patch("rugmunchbot.handlers.admin._run", return_value=(0, "running")),
    ):
        await cmd_services(update, ctx)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "RMI Services" in text
    assert "rmi-backend" in text


# ── /restart ───────────────────────────────────────────────────────────────


async def test_restart_requires_service_arg() -> None:
    update = _make_update(owner=True)
    ctx = _make_ctx([])
    with patch("rugmunchbot.handlers.admin.is_owner", return_value=True):
        await cmd_restart(update, ctx)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Usage" in text


async def test_restart_unknown_service() -> None:
    update = _make_update(owner=True)
    ctx = _make_ctx(["not-a-service"])
    with patch("rugmunchbot.handlers.admin.is_owner", return_value=True):
        await cmd_restart(update, ctx)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Unknown service" in text


async def test_restart_docker_success() -> None:
    update = _make_update(owner=True)
    ctx = _make_ctx(["rmi-backend"])
    with (
        patch("rugmunchbot.handlers.admin.is_owner", return_value=True),
        patch(
            "rugmunchbot.handlers.admin._run",
            side_effect=[(0, "running"), (0, "")],
        ),
    ):
        await cmd_restart(update, ctx)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "restarted" in text


# ── /logs ──────────────────────────────────────────────────────────────────


async def test_logs_requires_service_arg() -> None:
    update = _make_update(owner=True)
    ctx = _make_ctx([])
    with patch("rugmunchbot.handlers.admin.is_owner", return_value=True):
        await cmd_logs(update, ctx)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Usage" in text


async def test_logs_docker_success() -> None:
    update = _make_update(owner=True)
    ctx = _make_ctx(["rmi-backend"])
    with (
        patch("rugmunchbot.handlers.admin.is_owner", return_value=True),
        patch("rugmunchbot.handlers.admin._run", return_value=(0, "line1\nline2")),
    ):
        await cmd_logs(update, ctx)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Logs: rmi-backend" in text
    assert "line1" in text


# ── /budget ────────────────────────────────────────────────────────────────


async def test_budget_renders_usage() -> None:
    update = _make_update(owner=True)
    ctx = _make_ctx()
    usage = {
        "calls": 10,
        "spend": 0.01,
        "budget_remaining": 4.99,
        "calls_remaining": 140,
    }
    with (
        patch("rugmunchbot.handlers.admin.is_owner", return_value=True),
        patch(
            "rugmunchbot.tools.getxapi_budget.get_daily_usage",
            return_value=usage,
        ),
        patch(
            "rugmunchbot.tools.getxapi_config.GETXAPI_DAILY_CALL_LIMIT",
            150,
        ),
        patch("rugmunchbot.tools.getxapi_config.GETXAPI_TOTAL_BUDGET", 5.0),
    ):
        await cmd_budget(update, ctx)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "GetXAPI Budget" in text
    assert "10" in text


# ── /alerts ────────────────────────────────────────────────────────────────


async def test_alerts_redis_failure_graceful() -> None:
    update = _make_update(owner=True)
    ctx = _make_ctx()
    with (
        patch("rugmunchbot.handlers.admin.is_owner", return_value=True),
        patch(
            "redis.asyncio.Redis",
            side_effect=RuntimeError("redis down"),
        ),
    ):
        await cmd_alerts(update, ctx)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Could not read alerts" in text


# ── /sources ───────────────────────────────────────────────────────────────


async def test_sources_renders_counts() -> None:
    update = _make_update(owner=True)
    ctx = _make_ctx()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = {"c": 3}
    with (
        patch("rugmunchbot.handlers.admin.is_owner", return_value=True),
        patch("rugmunchbot.db.get_db", return_value=conn),
        patch(
            "rugmunchbot.tools.getxapi_config.GETXAPI_SECURITY_ACCOUNTS",
            ("a", "b"),
        ),
    ):
        await cmd_sources(update, ctx)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Active Content Sources" in text
    assert "RSS" in text


# ── /help ──────────────────────────────────────────────────────────────────


async def test_help_lists_all_commands() -> None:
    update = _make_update(owner=True)
    ctx = _make_ctx()
    with patch("rugmunchbot.handlers.admin.is_owner", return_value=True):
        await cmd_help(update, ctx)
    text = update.effective_message.reply_text.await_args.args[0]
    for cmd in (
        "/status",
        "/services",
        "/restart",
        "/logs",
        "/deploy",
        "/backup",
        "/alerts",
        "/budget",
        "/sources",
        "/help",
    ):
        assert cmd in text
