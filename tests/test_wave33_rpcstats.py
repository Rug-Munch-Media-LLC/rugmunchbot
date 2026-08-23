"""Wave 33 — /rpcstats owner command contracts (offline)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rugmunchbot.handlers.owner._rpcstats import cmd_rpcstats


def _update(uid: int):
    u = MagicMock()
    u.effective_user.id = uid
    u.message.reply_text = AsyncMock()
    return u


def _report():
    return {
        "summary": {
            "total_endpoints": 10,
            "alive": 7,
            "dead": 3,
            "total_calls": 100,
            "total_failures": 5,
            "freemium_escalations": 2,
            "freemium_calls_last_hour": 4,
        },
        "evm_chains": {
            "ethereum": [
                {"alive": True, "tier": "free"},
                {"alive": True, "tier": "free"},
                {"alive": True, "tier": "freemium"},
                {"alive": False, "tier": "free"},
            ],
        },
        "solana": {"a": {"alive": True, "tier": "free"}},
    }


@pytest.mark.asyncio
async def test_owner_gets_health_card():
    with (
        patch("rugmunchbot._vendor.scanner_engines.rpc_router.get_rpc_router") as gr,
        patch("rugmunchbot.handlers.owner._rpcstats.is_owner", return_value=True),
    ):
        gr.return_value.get_health_report.return_value = _report()
        upd = _update(123)
        await cmd_rpcstats(upd, MagicMock())
        text = upd.message.reply_text.call_args[0][0]
        assert "RPC Pool Health" in text
        assert "ethereum" in text
        assert "solana" in text
        assert "+1🔑" in text  # keyed freemium marker
        assert "Freemium escalations: <b>2</b>" in text


@pytest.mark.asyncio
async def test_non_owner_denied():
    with patch("rugmunchbot.handlers.owner._rpcstats.is_owner", return_value=False):
        upd = _update(999)
        await cmd_rpcstats(upd, MagicMock())
        upd.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_router_failure_degrades():
    with (
        patch(
            "rugmunchbot._vendor.scanner_engines.rpc_router.get_rpc_router",
            side_effect=RuntimeError("boom"),
        ),
        patch("rugmunchbot.handlers.owner._rpcstats.is_owner", return_value=True),
    ):
        upd = _update(123)
        await cmd_rpcstats(upd, MagicMock())
        assert "unavailable" in upd.message.reply_text.call_args[0][0]
