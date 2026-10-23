"""Partner strip contracts — every content lane, real links only, capped.

Covers: lane coverage, chain/venue context, the 3-link cap, the env kill
switch, idempotency, disclosure, and the null+reason path for Polymarket and
Hyperliquid (never a fabricated link).
"""

from __future__ import annotations

import re

import pytest

from rugmunchbot.channels.scheduler._core import _safe_send
from rugmunchbot.partners import (
    PARTNER_LANES,
    STRIP_MARK,
    footer_enabled,
    lane_for_content_type,
    partner_referrals,
    partner_strip,
    referral_for,
    referral_snapshot,
    with_partner_strip,
)
from rugmunchbot.referrals import DISCLOSURE

HREF_RE = re.compile(r'href="([^"]+)"')
TOKEN = "So11111111111111111111111111111111111111112"

#: Every host a configured partner link can legitimately use.
ALLOWED_HOSTS = {
    "jup.ag",
    "gmgn.ai",
    "t.me",
    "axiom.trade",
    "padre.trade",
    "polymarket.com",
}


def _hrefs(text: str) -> list[str]:
    return HREF_RE.findall(text)


def _hosts(text: str) -> set[str]:
    return {h.split("/")[2] for h in _hrefs(text)}


def test_lane_registry_covers_the_six_featured_lanes() -> None:
    assert set(PARTNER_LANES) == {
        "news",
        "alpha",
        "whales",
        "predictions",
        "scam_alert",
        "smart_money",
    }


@pytest.mark.parametrize("lane", ["news", "alpha", "whales", "smart_money", "scam_alert"])
def test_every_featured_lane_renders_links_with_disclosure(lane: str) -> None:
    strip = partner_strip(lane)
    assert STRIP_MARK in strip
    assert DISCLOSURE in strip
    assert 1 <= len(_hrefs(strip)) <= 3


def test_links_are_configured_never_invented() -> None:
    for lane in PARTNER_LANES:
        for chain in ("", "solana", "ethereum", "base"):
            strip = partner_strip(lane, chain=chain, token=TOKEN)
            assert _hosts(strip) <= ALLOWED_HOSTS
            for href in _hrefs(strip):
                assert href.startswith("https://")


def test_cap_is_three_and_env_can_lower_it(monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(_hrefs(partner_strip("alpha", token=TOKEN))) == 3
    monkeypatch.setenv("RMI_PARTNERS_MAX_LINKS", "1")
    assert len(_hrefs(partner_strip("alpha", token=TOKEN))) == 1
    monkeypatch.setenv("RMI_PARTNERS_MAX_LINKS", "0")
    assert partner_strip("alpha", token=TOKEN) == ""
    monkeypatch.setenv("RMI_PARTNERS_MAX_LINKS", "not-a-number")
    assert len(_hrefs(partner_strip("alpha", token=TOKEN))) == 3


def test_master_switch_off_removes_the_strip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RMI_PARTNERS_FOOTER", "0")
    assert footer_enabled() is False
    assert partner_strip("news") == ""
    assert with_partner_strip("post body", lane="news") == "post body"


def test_solana_and_evm_get_different_venues() -> None:
    sol = _hosts(partner_strip("alpha", chain="solana", token=TOKEN))
    evm = _hosts(partner_strip("alpha", chain="ethereum"))
    assert "gmgn.ai" in sol
    assert "gmgn.ai" in evm
    assert "jup.ag" not in evm  # Jupiter is Solana-only — never on an EVM post
    assert evm != sol


def test_token_deep_link_is_used_when_an_address_exists() -> None:
    strip = partner_strip("smart_money", chain="solana", token=TOKEN)
    assert f"gmgn.ai/sol/token/{TOKEN}" in strip


def test_predictions_lane_is_null_with_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RMI_REF_POLYMARKET", raising=False)
    assert partner_strip("predictions") == ""
    rows = partner_referrals("predictions")
    assert rows == [
        {
            "platform": "polymarket",
            "tier": "standard",
            "url": None,
            "account": None,
            "reason": "polymarket:not-configured",
        }
    ]
    monkeypatch.setenv("RMI_REF_POLYMARKET", "https://polymarket.com/?r=rmi")
    assert "polymarket.com" in _hosts(partner_strip("predictions"))


def test_hyperliquid_has_no_stored_code_and_never_a_link() -> None:
    ref = referral_for("hyperliquid")
    assert ref.url is None
    assert ref.as_dict()["reason"] == "hyperliquid:no-code-stored"
    assert "hyperliquid" not in partner_strip("whales").lower()
    # Still reported honestly in the full snapshot.
    assert any(row["platform"] == "hyperliquid" for row in referral_snapshot())


def test_strip_is_idempotent_and_dedupes_body_links() -> None:
    once = with_partner_strip("body", lane="news")
    assert with_partner_strip(once, lane="news") == once
    assert once.count(STRIP_MARK) == 1
    body = 'see <a href="https://gmgn.ai/?ref=rugmunch">GMGN</a>'
    deduped = with_partner_strip(body, lane="news")
    assert deduped.count("https://gmgn.ai/?ref=rugmunch") == 1


def test_env_code_override_replaces_or_drops_the_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RMI_REF_PADRE", "newcode")
    assert "ref=newcode" in partner_strip("alpha", chain="ethereum")
    monkeypatch.setenv("RMI_REF_GMGN", "")
    strip = partner_strip("news")
    assert '<a href="https://gmgn.ai/">GMGN.ai</a>' in strip  # code dropped
    # Jupiter URLs are owned by rugmunchbot.referrals (tier-aware), so the
    # strip never forks its own Jupiter link.
    assert referral_for("jupiter").url == "https://jup.ag/swap/SOL-USDC?ref=rugmunch"


def test_content_type_mapping_covers_the_lanes() -> None:
    assert lane_for_content_type("news") == "news"
    assert lane_for_content_type("whale") == "whales"
    assert lane_for_content_type("scan") == "smart_money"
    assert lane_for_content_type("scam_alert") == "scam_alert"
    assert lane_for_content_type("market") == ""
    assert lane_for_content_type(None) == ""


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id: str, text: str, **_: object) -> None:
        self.sent.append(text)


async def test_safe_send_appends_strip_only_for_funded_lanes() -> None:
    bot = _FakeBot()
    assert await _safe_send(bot, "123", "a news post", lane="news")
    assert STRIP_MARK in bot.sent[-1]
    assert DISCLOSURE in bot.sent[-1]
    assert await _safe_send(bot, "123", "a market post", lane="")
    assert STRIP_MARK not in bot.sent[-1]
