"""Wave 9 — _scan_report_sentinel: AEGIS flag renderers.

All expectations probed against the implementation before asserting (oracle
policy). The blue-chip branch is exercised via monkeypatched ``is_bluechip``
(the real allowlist import needs sqlalchemy, absent in the test env — its
ImportError fallback yields an empty set).
"""

from __future__ import annotations

import pytest

import rugmunchbot.formatters._scan_report_sentinel as srs
from rugmunchbot.formatters._scan_report_sentinel import _format_sentinel_pro, format_sentinel_flags


def _report(free: dict | None = None) -> dict:
    return {"address": "0xabc", "sentinel": {"free": free or {}}}


FULL_FREE = {
    "holder_analysis": {"hhi": 7200, "risk_level": "high"},
    "liquidity_verification": {"total_locked_percentage": 12.4, "liquidity_usd": 85000},
    "contract_authority": {"risk_level": "HIGH"},
    "proxy_detection": {"can_upgrade": True},
    "dev_reputation": {"reputation_level": "suspicious", "reputation_score": 31},
}


# ── guards ──────────────────────────────────────────────────────────────


def test_no_sentinel_dict_renders_nothing() -> None:
    assert format_sentinel_flags({}) == ""
    assert _format_sentinel_pro({}) == []
    assert format_sentinel_flags({"sentinel": "nope"}) == ""  # type: ignore[dict-item]


def test_bluechip_suppression(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srs, "is_bluechip", lambda addr: True)
    out = format_sentinel_flags(_report(FULL_FREE))
    assert "Known blue-chip" in out
    assert "concentration & lock signals suppressed" in out
    # Even a fully-flagged report shows ONLY the suppression line.
    assert "HHI" not in out
    pro = _format_sentinel_pro(_report(FULL_FREE))
    assert len(pro) == 1
    assert "Known blue-chip" in pro[0]


# ── free-tier block ─────────────────────────────────────────────────────


def test_free_block_full_flags() -> None:
    out = format_sentinel_flags(_report(FULL_FREE))
    assert "🛡️ <b>AEGIS</b>" in out
    assert "⚠️ High holder concentration (HHI: <b>7200</b>)" in out
    assert "🔐 LP Lock: <b>12%</b>" in out
    assert "🔒 Contract authority risk: <b>HIGH</b>" in out
    assert "⚠️ Upgradeable proxy (admin can change implementation)" in out
    assert "👨‍💻 Dev reputation: <b>suspicious</b> (31/100)" in out


def test_free_block_green_when_no_flags() -> None:
    out = format_sentinel_flags(_report({}))
    assert "✅ No AEGIS red flags" in out


def test_free_block_low_concentration_includes_level() -> None:
    out = format_sentinel_flags(_report({"holder_analysis": {"hhi": 1500, "risk_level": "low"}}))
    assert "👥 Holder concentration (HHI: <b>1500</b>, low)" in out


# ── pro renderer ────────────────────────────────────────────────────────


def test_pro_full_bucket_lines() -> None:
    lines = _format_sentinel_pro(_report(FULL_FREE))
    assert lines[0] == "🔴 Holder concentration: HHI <b>7200</b> (high)"
    assert "🔴 LP Locked: <b>12%</b>" in lines
    assert "🪙 Liquidity USD: <b>$85.0K</b>" in lines
    assert "🔴 Contract authority risk: <b>HIGH</b>" in lines
    assert "⚠️ Upgradeable proxy (admin can change implementation)" in lines
    assert "🔴 Dev reputation: <b>suspicious</b> (31/100)" in lines


@pytest.mark.parametrize(
    ("locked", "emoji"),
    [(12.4, "🔴"), (50.0, "🟡"), (95.0, "🟢")],
)
def test_pro_lp_lock_emoji_tiers(locked: float, emoji: str) -> None:
    lines = _format_sentinel_pro(
        _report({"liquidity_verification": {"total_locked_percentage": locked}})
    )
    assert f"{emoji} LP Locked: <b>{locked:.0f}%</b>" in lines


@pytest.mark.parametrize(
    ("level", "emoji"),
    [
        ("high", "🔴"),
        ("medium", "🟠"),
        ("low", "🟢"),
    ],  # medium renders orange (U+1F7E0), not yellow
)
def test_pro_hhi_emoji_by_risk_level(level: str, emoji: str) -> None:
    lines = _format_sentinel_pro(_report({"holder_analysis": {"hhi": 2500, "risk_level": level}}))
    assert f"{emoji} Holder concentration: HHI <b>2500</b> ({level})" in lines


def test_pro_medium_contract_authority_yellow() -> None:
    lines = _format_sentinel_pro(_report({"contract_authority": {"risk_level": "medium"}}))
    assert "🟡 Contract authority: <b>MEDIUM</b>" in lines


def test_pro_proxy_type_line_when_not_upgradeable() -> None:
    lines = _format_sentinel_pro(
        _report({"proxy_detection": {"is_proxy": True, "proxy_type": "UUPS"}})
    )
    assert "📦 Proxy contract (UUPS)" in lines


def test_pro_dev_rep_without_score_shows_na() -> None:
    lines = _format_sentinel_pro(_report({"dev_reputation": {"reputation_level": "dangerous"}}))
    assert "🔴 Dev reputation: <b>dangerous</b> (N/A)" in lines


def test_pro_green_when_no_flags() -> None:
    assert _format_sentinel_pro(_report({})) == ["✅ No AEGIS red flags"]
