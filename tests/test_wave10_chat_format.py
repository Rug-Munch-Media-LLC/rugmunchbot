"""Wave 10 — chat_ai._format: tool-answer markdown + bridge action buttons.

All expectations probed against the implementation before asserting (oracle
policy). Output is MARKDOWN (**bold**, `code`) — never raw HTML — because it
flows through the untrusted-LLM escape pipeline downstream.
"""

from __future__ import annotations

from rugmunchbot.handlers.chat_ai._format import _format_tool_answer, _render_bridge_actions
from rugmunchbot.tools.registry import ToolResult


def _tr(tool: str, data: object) -> ToolResult:
    return ToolResult(tool=tool, ok=True, data=data, source="test")


def test_price_block_markdown_and_live_footer() -> None:
    res = {
        "price_lookup": _tr(
            "price_lookup",
            {
                "pairs": [
                    {
                        "name": "Bonk",
                        "symbol": "BONK",
                        "chain": "solana",
                        "address": "A" * 44,
                        "price_usd": 0.00002345,
                        "change_24h": -5.2,
                        "market_cap": 1_700_000_000,
                        "liquidity_usd": 12_000_000,
                        "volume_24h": 45_000_000,
                        "fdv": 2_300_000_000,
                    }
                ]
            },
        )
    }
    out = _format_tool_answer(res)
    assert out is not None
    assert "🪙 **Bonk** (BONK)" in out
    assert "`AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`" in out
    assert "💰 $0.0000234500 | 24h: -5.20%" in out
    assert "📈 MCap: $1.70B" in out
    assert "💧 Liq $12.00M | Vol $45.00M | FDV $2.30B" in out
    assert "*Live data via Rug Munch tools.*" in out


def test_security_block_risk_emoji_tiers() -> None:
    for score, emoji in [(30, "🟢"), (65, "🟡"), (85, "🔴")]:
        out = _format_tool_answer(
            {"security_scan": _tr("security_scan", {"risk_score": score, "is_honeypot": False})}
        )
        assert out is not None
        assert f"**🔍 Risk: {emoji} {score}/100**" in out


def test_web_only_fallback_gets_knowledge_footer() -> None:
    res = {"web_search": _tr("web_search", {"context": "Knowledge: WETH is wrapped ETH."})}
    out = _format_tool_answer(res)
    assert out is not None
    assert "Knowledge: WETH is wrapped ETH." in out
    assert "*Rug Munch knowledge — live web unavailable.*" in out
    assert "Live data via" not in out


def test_nothing_usable_returns_none() -> None:
    assert _format_tool_answer({}) is None
    failed = ToolResult(tool="price_lookup", ok=False, data={}, source="t", error="down")
    assert _format_tool_answer({"price_lookup": failed}) is None


def test_trending_block_lists_tokens() -> None:
    res = {
        "trending": _tr(
            "trending",
            {
                "tokens": [
                    {"symbol": "WIF", "chain": "solana", "address": "B" * 44, "change_24h": 12.5},
                    {"symbol": "MOG", "chain": "base", "address": "C" * 44},
                ]
            },
        )
    }
    out = _format_tool_answer(res)
    assert out is not None
    assert "**🔥 Trending**" in out
    assert "• $WIF ◎ Solana | 12.50% — `" in out
    assert "• $MOG" in out  # no change → no pct segment


# ── bridge actions ──────────────────────────────────────────────────────


def test_bridge_actions_analyze_only() -> None:
    markup = _render_bridge_actions(
        [
            {"action": "analyze", "label": "Scan it", "target": "0xabc"},
            {"action": "exploit", "label": "dead", "target": "y"},  # no surface → skipped
            "garbage",  # non-dict → skipped
        ]
    )
    assert markup is not None
    assert len(markup.inline_keyboard) == 1
    btn = markup.inline_keyboard[0][0]
    assert btn.text == "Scan it"
    assert btn.callback_data == "scan_0xabc_unknown"


def test_bridge_actions_empty_returns_none() -> None:
    assert _render_bridge_actions([]) is None
    assert _render_bridge_actions(None) is None
    assert _render_bridge_actions([{"action": "exploit", "label": "x", "target": "y"}]) is None
