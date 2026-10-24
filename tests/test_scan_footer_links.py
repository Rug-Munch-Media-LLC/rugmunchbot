"""Scan-footer link composition: referral order, the 3-link cap, one disclosure.

The canonical URL builders themselves are locked in ``test_gmgn_canonical``;
this file only asserts what the footer COMPOSES from them, so a future partner
list change cannot turn the strip back into an 8-link referral wall.
"""

from __future__ import annotations

import re

import pytest

from rugmunchbot.formatters._base._links import footer_links
from rugmunchbot.referrals import DISCLOSURE

TOKEN = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
HREF_RE = re.compile(r'href="([^"]+)"')

#: Hrefs the footer emits for navigation only — never counted as referral links.
_UTILITY_HOSTS = ("t.me", "rugmunch.io", "dexscreener", "dextools")


def _is_referral(href: str) -> bool:
    return not any(host in href for host in _UTILITY_HOSTS)


def _referral_hrefs(markup: str) -> list[str]:
    """Footer links that go to a partner (i.e. carry our attribution)."""
    return [href for href in HREF_RE.findall(markup) if _is_referral(href)]


@pytest.fixture
def scan_footer() -> str:
    return footer_links(TOKEN, "solana", purpose="scan")


def test_scan_footer_leads_with_jupiter_then_gmgn(scan_footer: str) -> None:
    refs = _referral_hrefs(scan_footer)
    assert refs[0].startswith("https://jup.ag/swap/SOL-")
    assert refs[1].startswith("https://gmgn.ai/sol/token/rugmunch_")


def test_scan_footer_caps_referral_links_at_three(scan_footer: str) -> None:
    # Referral links (Jupiter + GMGN plus at most one more) never exceed three.
    assert len(_referral_hrefs(scan_footer)) <= 3


def test_scan_footer_carries_one_disclosure(scan_footer: str) -> None:
    assert scan_footer.count(DISCLOSURE) == 1
