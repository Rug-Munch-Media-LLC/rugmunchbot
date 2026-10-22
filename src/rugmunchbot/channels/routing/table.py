"""Canonical channel routing table — one source for what each channel may carry.

Keyed by channel role (the same roles ``app/core/channels_registry.py`` and
``rugmunchbot/channels/style_guides.py`` use). Every poster consults this table
through :func:`rugmunchbot.channels.routing.gate.decide` instead of hard-coding
a severity floor or an interval.

Doctrine encoded here (2026-10-22 channel audit):

* ``rmialerts`` — real-time SECURITY alerts only (exploit / rug / scam / shill
  / imminent-danger detections). critical+high, event-driven, <=5/hour. A
  routine scanner risk batch is NOT alert content.
* ``rmialpha`` — whale flows only. Launches are redirected to ``rmiscans``.
* ``rmiscans`` — launches + deep scan reports.
* ``rmicryptonews`` — news-worthy stories (24/7 news surface).
* ``rmiupdates`` — product releases only, one post per day at most.
* ``munchscans`` — archived dead test duplicate; never post.
* ``crmunchers`` — manual-only community channel; the bot never auto-posts.
"""

from __future__ import annotations

from dataclasses import dataclass

#: severity name -> rank. Unknown severities rank 0 and never pass a floor.
SEVERITY_RANK: dict[str, int] = {"low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass(frozen=True, slots=True)
class ChannelRoute:
    """One channel's routing policy."""

    channel: str
    handle: str
    categories: frozenset[str] | None
    min_severity: str
    max_per_hour: int
    event_driven: bool = False
    archived: bool = False
    manual_only: bool = False
    note: str = ""


SECURITY_CATEGORIES = frozenset(
    {
        "exploit",
        "hack",
        "security_incident",
        "rug_pull",
        "rug_confirmed",
        "scam",
        "scam_call",
        "shill",
        "shill_campaign",
        "honeypot",
        "mint",
        "blacklist",
        "liquidity_remove",
        "lp_expiry",
        "deployer_sell",
        "alert",
    }
)

ROUTING_TABLE: dict[str, ChannelRoute] = {
    "rmialerts": ChannelRoute(
        channel="rmialerts",
        handle="@rmialerts",
        categories=SECURITY_CATEGORIES,
        min_severity="high",
        max_per_hour=5,
        event_driven=True,
        note="real-time security alerts; critical/high only, never a routine risk batch",
    ),
    "rmialpha": ChannelRoute(
        channel="rmialpha",
        handle="@rmialpha",
        categories=frozenset({"whale", "whale_movement"}),
        min_severity="low",
        max_per_hour=4,
        event_driven=True,
        note="whale flows only; token launches belong to @rmiscans",
    ),
    "rmiscans": ChannelRoute(
        channel="rmiscans",
        handle="@rmiscans",
        categories=frozenset({"launch", "scanner", "scan", "scan_report"}),
        min_severity="low",
        max_per_hour=6,
        note="token launches + deep scan reports",
    ),
    "rmicryptonews": ChannelRoute(
        channel="rmicryptonews",
        handle="@rmicryptonews",
        categories=frozenset({"news", "bulletin", "cex_flow", "market", "alpha"}),
        min_severity="low",
        max_per_hour=30,
        note="24/7 news surface",
    ),
    "rmiupdates": ChannelRoute(
        channel="rmiupdates",
        handle="@rmiupdates",
        categories=frozenset({"release", "shipped"}),
        min_severity="low",
        max_per_hour=1,
        event_driven=True,
        note="product releases only; silent when nothing shipped",
    ),
    "cryptorugmuncher": ChannelRoute(
        channel="cryptorugmuncher",
        handle="@cryptorugmuncher",
        categories=None,
        min_severity="critical",
        max_per_hour=9,
        note="main channel; importance-gated elsewhere, daily cap via _main_admission",
    ),
    "munchscans": ChannelRoute(
        channel="munchscans",
        handle="@munchscans",
        categories=None,
        min_severity="critical",
        max_per_hour=0,
        archived=True,
        note="dead test duplicate of @rmiscans — archived 2026-10-22, never post",
    ),
    "crmunchers": ChannelRoute(
        channel="crmunchers",
        handle="@crmunchers",
        categories=None,
        min_severity="critical",
        max_per_hour=0,
        manual_only=True,
        note="manual-only community channel (unmoderated promo spam) — never auto-post",
    ),
}

#: Categories that belong on another channel when they arrive at the wrong one.
REDIRECTS: dict[str, str] = {
    "launch": "rmiscans",
    "scan": "rmiscans",
    "scan_report": "rmiscans",
}


def policy_for(channel: str) -> ChannelRoute | None:
    """Return the routing policy for *channel* (``None`` when unregistered)."""
    return ROUTING_TABLE.get(channel)
