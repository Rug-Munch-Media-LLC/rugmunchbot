"""`/whales` and `/hlwhales` are ONE renderer, one path (2026-10-25).

The old ``handlers/hlwhales.py`` re-formatted the same Hyperliquid feed with
bare ``float()`` coercions: a malformed ``change_24h`` crashed the command.
These tests lock the consolidation — both names resolve to the same callback,
the canonical render is byte-stable, and malformed/None/string payloads never
raise. *fetch* is injected through the handler's DI seam (no monkeypatch).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rugmunchbot.handlers import smart_money
from rugmunchbot.smart_money.types import Feed

MALFORMED = Feed(
    kind="hl",
    updated_at="2026-10-25T09:00:00Z",
    source="hyperliquid.metaAndAssetCtxs",
    tried=("market/hyperliquid-action",),
    items=(
        {
            "kind": "mover",
            "direction": "up",
            "asset": "BTC",
            "change_24h_pct": "not-a-number",
            "volume_24h": "junk",
            "open_interest_usd": None,
            "funding_rate": "abc",
            "price": None,
        },
        {
            "kind": "mover",
            "direction": "down",
            "asset": "ETH",
            "change_24h_pct": None,
            "volume_24h": None,
            "open_interest_usd": "n/a",
            "funding_rate": None,
        },
        {
            "kind": "squeeze",
            "direction": "long_squeeze",
            "asset": "SOL",
            "funding_rate": "oops",
            "open_interest_usd": [],
        },
        {
            "kind": "cluster",
            "asset": None,
            "open_interest_usd": {},
            "funding_rate": {},
            "liquidation_pressure": "x",
            "oi_share_pct": None,
            "long_liq_price_10x": None,
            "short_liq_price_10x": None,
        },
    ),
)


class _Msg:
    def __init__(self) -> None:
        self.text = ""

    async def reply_text(self, *a, **k):
        return self

    async def edit_text(self, text, *a, **k):
        self.text = text
        return self


class _App:
    def __init__(self) -> None:
        self.handlers = []

    def add_handler(self, handler):
        self.handlers.append(handler)


def _update() -> tuple[SimpleNamespace, _Msg]:
    msg = _Msg()
    return SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=1)), msg


async def _render(feed: Feed) -> str:
    async def fake_fetch(limit: int = 6) -> Feed:
        return feed

    update, msg = _update()
    await smart_money.cmd_whales(update, SimpleNamespace(args=[]), fetch=fake_fetch)
    return msg.text


def test_both_command_names_share_one_callback():
    app = _App()
    smart_money.register_smart_money(app)
    wired = {
        name: h.callback
        for h in app.handlers
        for name in h.commands
        if name in smart_money.HL_COMMANDS
    }
    assert set(wired) == {"whales", "hlwhales"}
    assert wired["whales"] is wired["hlwhales"] is smart_money.cmd_whales


@pytest.mark.asyncio
async def test_malformed_payload_never_raises_and_degrades_to_na():
    text = await _render(MALFORMED)
    assert "HYPERLIQUID" in text or "FUNDING" in text
    assert "n/a" in text
    assert "not-a-number" not in text
    assert "abc" not in text


@pytest.mark.asyncio
async def test_mover_golden_output_is_byte_stable():
    feed = Feed(
        kind="hl",
        updated_at="2026-10-25T09:00:00Z",
        source="hyperliquid.metaAndAssetCtxs",
        items=(
            {
                "kind": "mover",
                "direction": "up",
                "asset": "BTC",
                "change_24h_pct": 7.5,
                "volume_24h": 2_500_000_000,
                "open_interest_usd": 1_200_000,
                "funding_rate": 0.0001,
            },
        ),
    )
    text = await _render(feed)
    assert text.startswith("🐋 <b>HYPERLIQUID · BTC PUMP +7.5%</b>")
    assert "24h volume <b>$2.50B</b> · OI $1.20M" in text
    assert "Funding: +0.0100%/h" in text
    assert "<b>Source:</b> Hyperliquid metaAndAssetCtxs via rmi-backend" in text
    assert "<b>Time:</b> 2026-10-25 09:00Z" in text
    assert "<b>Risk:</b>" in text


@pytest.mark.asyncio
async def test_feed_down_renders_honest_fallback():
    text = await _render(
        Feed(
            kind="hl", note="Hyperliquid snapshot unavailable", tried=("market/hyperliquid-action",)
        )
    )
    assert "unavailable" in text
    assert "market/hyperliquid-action" in text
