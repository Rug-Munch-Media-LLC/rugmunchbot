"""Test wave 3 — intent list rendering, alert verdicts, tool prompt blocks.

Behavior documented from actual outputs (2026-08-21). Pure construction
only: no network, no DB, no telegram network calls.
"""

from __future__ import annotations

from rugmunchbot.channels.formatters import alert_verdict
from rugmunchbot.tools.intent import _compact_list, build_tool_prompt_block
from rugmunchbot.tools.registry import ToolResult

_TOKENS = [
    {"symbol": "BONK", "name": "Bonk", "chain": "solana", "change_24h": 45.2, "address": "9xQabc"},
    {
        "symbol": "WIF",
        "name": "dogwifhat",
        "chain": "solana",
        "change_24h": -3.1,
        "address": "9xQdef",
    },
]


def test_compact_list_renders_fields_in_order() -> None:
    out = _compact_list(_TOKENS, cap=5)
    line = out.splitlines()[0]
    assert line.startswith("{symbol=BONK")
    assert "name=Bonk" in line
    assert "chain=solana" in line
    assert "address=9xQabc" in line
    assert "change_24h=+45.2%" in line


def test_compact_list_signed_pct() -> None:
    out = _compact_list(_TOKENS, cap=5)
    assert "change_24h=-3.1%" in out


def test_compact_list_respects_cap() -> None:
    out = _compact_list(_TOKENS, cap=1)
    assert "BONK" in out
    assert "WIF" not in out


def test_alert_verdict_high_risk() -> None:
    verdict = alert_verdict({"risk_score": 85, "attack_vector": "honeypot"})
    assert "RugMunch scanner" in verdict
    assert "verify" in verdict.lower()


def test_build_tool_prompt_block_renders_tool_and_source() -> None:
    result = ToolResult(
        tool="price_lookup", ok=True, data={"price_usd": 1234.5}, source="unit-test"
    )
    block = build_tool_prompt_block({"price_lookup": result})
    assert isinstance(block, str)
    assert "price_lookup" in block
    assert "unit-test" in block
