"""Contextual partner selection — chain/venue aware, capped, env-controlled.

Master switch ``RMI_PARTNERS_FOOTER`` (default on) and cap
``RMI_PARTNERS_MAX_LINKS`` (default 3, clamped to 0-3) are read per call so
ops can change featuring without a deploy.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ._catalog import referral_for
from ._lanes import LANE_PARTNERS, normalize_lane

if TYPE_CHECKING:
    from rugmunchbot.referrals import Referral

_OPT_IN = ("1", "true", "yes", "on")

#: Featured links per post — "heavily featured but not spam".
DEFAULT_MAX_LINKS = 3
MAX_LINKS_CEILING = 3

#: Venues whose own lane wins over the lane table when passed explicitly.
_VENUE_KEYS: dict[str, tuple[str, ...]] = {
    "polymarket": ("polymarket",),
    "hyperliquid": ("hyperliquid", "gmgn", "banana", "jupiter"),
    "hl": ("hyperliquid", "gmgn", "banana", "jupiter"),
}


def footer_enabled() -> bool:
    """True unless ``RMI_PARTNERS_FOOTER`` turns the strip off."""
    raw = os.environ.get("RMI_PARTNERS_FOOTER")
    if raw is None:
        return True
    return raw.strip().lower() in _OPT_IN


def max_links() -> int:
    """Configured link cap, clamped to 0..3 (bad values fall back to 3)."""
    raw = os.environ.get("RMI_PARTNERS_MAX_LINKS")
    if raw is None:
        return DEFAULT_MAX_LINKS
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_LINKS
    return max(0, min(value, MAX_LINKS_CEILING))


def _candidate_keys(lane: str, venue: str) -> tuple[str, ...]:
    if venue in _VENUE_KEYS:
        return _VENUE_KEYS[venue]
    return LANE_PARTNERS.get(lane, ())


def select_partners(
    lane: str | None, *, chain: str = "", token: str = "", venue: str = ""
) -> list[Referral]:
    """Available partners for a lane, deduped by URL and capped."""
    lane_key = normalize_lane(lane)
    if not lane_key or not footer_enabled():
        return []
    venue_key = (venue or "").strip().lower()
    refs: list[Referral] = []
    seen: set[str] = set()
    for key in _candidate_keys(lane_key, venue_key):
        ref = referral_for(key, token=token, chain=chain)
        if not ref.available or not ref.url or ref.url in seen:
            continue
        seen.add(ref.url)
        refs.append(ref)
    return refs[: max_links()]


def partner_referrals(
    lane: str | None, *, chain: str = "", token: str = "", venue: str = ""
) -> list[dict[str, str | None]]:
    """Every lane candidate as data — unavailable rows keep null + reason."""
    lane_key = normalize_lane(lane)
    if not lane_key:
        return []
    return [
        referral_for(key, token=token, chain=chain).as_dict()
        for key in _candidate_keys(lane_key, (venue or "").strip().lower())
    ]
