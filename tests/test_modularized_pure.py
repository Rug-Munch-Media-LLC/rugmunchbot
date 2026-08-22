"""Pure-function coverage for the modularized packages (2026-08-21).

Targets the highest-value deterministic surfaces carved out during the
de-godding waves: config display helpers, intent detection/routing, and
formatter primitives/links. No I/O, no network, no telegram.
"""

from __future__ import annotations

import pytest

from rugmunchbot.config import (
    _risk_gauge,
    fmt_chain,
    fmt_number,
    fmt_pct,
    risk_bar,
    threat_indicator,
)
from rugmunchbot.formatters import (
    detect_chain,
    dexscreener_url,
    explorer_url,
    gmgn_url,
    is_evm,
    is_sol,
    sep,
    short_addr,
    thin_sep,
)
from rugmunchbot.tools.intent import (
    detect_exclusions,
    detect_negations,
    detect_risk_rank,
    extract_chain,
    route_tools,
    tool_kwargs,
)

# ── config/_display ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("score", "prefix", "tier"),
    [
        (95, "\U0001f534", "DANGER"),
        (70, "\U0001f7e0", "RISKY"),
        (50, "\U0001f7e1", "CAUTION"),
        (30, "\U0001f7e2", "LOW"),
        (5, "\u2b1c", "SAFE"),
    ],
)
def test_risk_bar_tiers(score: float, prefix: str, tier: str) -> None:
    bar = risk_bar(score)
    assert bar.startswith(prefix)
    assert tier in bar
    assert f"{float(score)}%" in bar


def test_risk_bar_none_is_safe() -> None:
    assert "SAFE" in risk_bar(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("score", "expected"),
    [(85, "EXTREME"), (65, "HIGH"), (45, "MODERATE"), (25, "LOW"), (5, "SAFE")],
)
def test_risk_gauge(score: float, expected: str) -> None:
    assert expected in _risk_gauge(score)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "N/A"),
        (1.5e9, "$1.50B"),
        (2.3e6, "$2.30M"),
        (4500.0, "$4.5K"),
        (0.5, "$0.500000"),
        (0.001, "$0.0010000000"),
    ],
)
def test_fmt_number(value, expected: str) -> None:
    assert fmt_number(value) == expected


def test_fmt_pct() -> None:
    assert fmt_pct(12.345) == "12.35%"
    assert fmt_pct(None) == "N/A"


def test_fmt_chain_known_and_unknown() -> None:
    assert fmt_chain("solana").startswith("◎")
    assert "Ethereum" in fmt_chain("ethereum")
    assert "⛓" in fmt_chain("sui")


def test_threat_indicator() -> None:
    assert threat_indicator("mint", True).startswith("\U0001f534")
    assert threat_indicator("mint", False).startswith("✅")


# ── formatters/_base primitives + links ──────────────────────────────────────


def test_chain_detection() -> None:
    assert is_evm("0x" + "a" * 40)
    assert not is_evm("9xQ" + "a" * 40)
    assert is_sol("9xQ" + "a" * 40)
    assert detect_chain("0x" + "b" * 40) == "ethereum"
    assert detect_chain("9xQ" + "b" * 40) == "solana"


def test_short_addr() -> None:
    addr = "0x" + "c" * 40
    out = short_addr(addr)
    assert out.startswith("0x")
    assert "…" in out or "..." in out
    assert len(out) < len(addr)


def test_separators_differ() -> None:
    assert sep() != thin_sep()
    assert len(sep()) >= len(thin_sep())


def test_dex_urls_embed_address() -> None:
    addr = "So11111111111111111111111111111111111111112"
    for url in (dexscreener_url(addr, "solana"), gmgn_url(addr, "solana")):
        assert addr in url
        assert url.startswith("https://")


def test_explorer_url_by_chain() -> None:
    evm = "0x" + "d" * 40
    sol = "9xQ" + "d" * 40
    assert "etherscan" in explorer_url(evm, "ethereum").lower()
    assert "solscan" in explorer_url(sol, "solana").lower()


# ── tools/intent ─────────────────────────────────────────────────────────────


def test_detect_negations() -> None:
    assert "trending" in detect_negations("what's NOT trending right now?")


def test_detect_exclusions_suppress() -> None:
    excl = detect_exclusions("show me gainers but don't show me trending")
    assert "trending" in excl


@pytest.mark.parametrize(
    ("question", "chain"),
    [
        ("price of BONK on solana", "solana"),
        # documents current first-match-wins behavior: ETH beats the later chain word
        ("swap ETH on base", "ethereum"),
        ("top tokens", ""),
    ],
)
def test_extract_chain(question: str, chain: str | None) -> None:
    assert extract_chain(question) == chain


def test_detect_risk_rank() -> None:
    assert detect_risk_rank("riskiest tokens this week") is not None


def test_route_tools_price_ask() -> None:
    tools = route_tools("price of BONK")
    assert "price_lookup" in tools


def test_route_tools_scan_with_address() -> None:
    addr = "0x" + "e" * 40
    tools = route_tools(f"scan {addr}")
    assert any(t in tools for t in ("scanner_intel", "security_scan", "token_meta"))


def test_tool_kwargs_address_binding() -> None:
    addr = "0x" + "f" * 40
    kw = tool_kwargs(f"scan {addr}", ["scanner_intel"])
    assert kw["scanner_intel"]["address"] == addr
