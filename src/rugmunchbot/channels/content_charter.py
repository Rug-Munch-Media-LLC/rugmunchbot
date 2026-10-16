"""Per-channel content charter — the ONE authoritative channel contract.

Operator directive (2026-10-17): content must be CENTERED per channel. There
was a real defect — TWO competing ``CHANNEL_CONTENT_TYPES`` dictionaries with
INCOMPATIBLE vocabularies and TWO ``channel_accepts_content`` functions:

* ``_vendor/channels_registry.py`` — semantic types (``market_commentary``,
  ``breaking_news``, ``exploit``, …), allowlist (blocks unknown channel).
* ``channels/style_guides.py`` — short types (``market``, ``alert``, ``scan``,
  …), fail-soft (returns True for unknown).

A post could pass one gate and be silently dropped/blocked by the other. The
LIVE vocabulary is the SHORT one: every send site passes ``market``, ``alert``,
``scan``, ``whale``, ``alpha``, ``shill``, ``news``, ``roundup``, ``update``.
This module is now the single source of truth; the vendor duplicate is deleted
and ``style_guides`` re-exports from here.

The charter binds, per channel: its PURPOSE, the content TYPES it accepts, the
GRAPHIC each type carries (wired to ``support.post_graphics.POST_TYPE_GRAPHICS``
— the operator's original images), and its CADENCE.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from rugmunchbot.support.post_graphics import graphic_for

# ══════════════════════════════════════════════════════════════════════════
# CANONICAL CONTENT TYPES (the ONE vocabulary)
# ══════════════════════════════════════════════════════════════════════════
# Lowercase, short. These are exactly what the poster passes at every send
# site — do not invent synonyms; a new type is a new row here AND a call site.
#
#   market    — market commentary / daily wrap (flagship longform)
#   roundup   — daily roundup / digest
#   news      — breaking crypto news (sourced, dated, linked)
#   alert     — security alert: hacks, exploits, rug pulls, scam warnings
#   scan      — suspicious-token scan (verdict + risk score)
#   whale     — whale wallet movement
#   alpha     — smart-money / data-driven market signal
#   shill     — coordinated shill-campaign intel
#   scam_alert— verified scam warning (distinct from a live exploit alert)
#   update    — product update / release note
#   system    — system notification (health, ops, non-security status)
CANONICAL_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "market",
        "roundup",
        "news",
        "alert",
        "scan",
        "whale",
        "alpha",
        "shill",
        "scam_alert",
        "update",
        "system",
    }
)


@dataclass(frozen=True)
class ChannelCharter:
    """One channel's authoritative content contract."""

    role: str
    handle: str
    purpose: str
    content_types: frozenset[str]
    cadence: str

    def graphic_for(self, content_type: str) -> str:
        """The brand graphic URL for *content_type* on this channel.

        Delegates to ``post_graphics.graphic_for`` (the operator's original
        images) so the charter and the poster never disagree on the image.
        """
        return graphic_for(content_type)


# ══════════════════════════════════════════════════════════════════════════
# THE CHARTER — one row per channel we run
# ══════════════════════════════════════════════════════════════════════════
# ``main`` is the additive flagship: it accepts every content type so
# escalations from alerts/scans/alpha/news reach the broader audience (its
# ≤10/day budget is enforced separately by ``channels.main_cap``).
CHANNEL_CHARTER: Final[dict[str, ChannelCharter]] = {
    "main": ChannelCharter(
        role="main",
        handle="@cryptorugmuncher",
        purpose=(
            "Flagship. Top-tier anything — market commentary, daily wrap, "
            "breaking security news, shill campaigns — plus escalations from "
            "every lane. Never filler; if nothing clears the bar, post nothing."
        ),
        content_types=frozenset(
            {"market", "roundup", "news", "alert", "scan", "whale", "alpha", "shill", "scam_alert"}
        ),
        cadence="event-driven + daily wrap; ≤10 posts/day (main_cap)",
    ),
    "news": ChannelCharter(
        role="news",
        handle="@rmicryptonews",
        purpose=(
            "Breaking crypto news ONLY. Source-quality gated, deduped across "
            "lanes, importance-reranked. Every post: link required, dated, "
            "sourced. No curation, no commentary beyond one RugMunch angle line."
        ),
        content_types=frozenset({"news"}),
        cadence="event-driven (news ingest) + scheduled sweeps",
    ),
    "alerts": ChannelCharter(
        role="alerts",
        handle="@rmialerts",
        purpose=(
            "Security alerts ONLY — hacks, exploits, rug pulls, verified scams, "
            "LP bombs in progress. NEVER price-movement-only items, whale moves, "
            "scans, or system notifications. Every post carries a verdict + score."
        ),
        content_types=frozenset({"alert", "scam_alert"}),
        cadence="event-driven (scanner/exploit hooks) + regular digest",
    ),
    "alpha": ChannelCharter(
        role="alpha",
        handle="@rmialpha",
        purpose=(
            "Whale + smart-money signals ONLY, from our own ClickHouse data. "
            "No news, no scans. Show the signal, label the risk, link evidence."
        ),
        content_types=frozenset({"whale", "alpha"}),
        cadence="scheduled sweeps (whale watcher + smart-money)",
    ),
    "scans": ChannelCharter(
        role="scans",
        handle="@rmiscans",
        purpose=(
            "Suspicious-token scans ONLY — the day's most notable scanned tokens, "
            "reranker-gated, one compact card per token (verdict + risk + chain). "
            "No news, no whale alerts."
        ),
        content_types=frozenset({"scan"}),
        cadence="~10/day (scans of note)",
    ),
    "updates": ChannelCharter(
        role="updates",
        handle="@rmiupdates",
        purpose=(
            "Product updates / release notes ONLY. No news, no alerts. Health/ops status digest."
        ),
        content_types=frozenset({"update"}),
        cadence="daily digest",
    ),
    "shills": ChannelCharter(
        role="shills",
        handle="@rmishills",
        purpose=(
            "Shill-campaign intel ONLY — coordinated pump-group / paid-shill "
            "tracking. No news, no scans, no whale moves."
        ),
        content_types=frozenset({"shill"}),
        cadence="event-driven (shill-watch)",
    ),
}

#: Channel role → the set of content types it is chartered to post.
#: Derived from the charter so the two can never drift.
CHANNEL_CONTENT_TYPES: Final[dict[str, frozenset[str]]] = {
    role: charter.content_types for role, charter in CHANNEL_CHARTER.items()
}


def channel_accepts_content(channel: str, content_type: str) -> bool:
    """True when *channel* is chartered to post *content_type*.

    Fail-soft by design: an unknown channel or an unknown/empty content type
    returns True so a mislabeled caller can never silently drop a post — the
    gate only blocks a KNOWN channel posting a KNOWN content type it is not
    chartered for. Callers pass the channel ROLE (``"alerts"``, ``"scans"``,
    ``"alpha"``, …), not the public handle.
    """
    if not channel or not content_type:
        return True
    accepted = CHANNEL_CONTENT_TYPES.get(str(channel).strip().lower())
    if accepted is None:
        return True
    return str(content_type).strip().lower() in accepted


def charter_for(channel: str) -> ChannelCharter | None:
    """The charter row for *channel* (None when unknown)."""
    if not channel:
        return None
    return CHANNEL_CHARTER.get(str(channel).strip().lower())


__all__ = [
    "CANONICAL_CONTENT_TYPES",
    "CHANNEL_CHARTER",
    "CHANNEL_CONTENT_TYPES",
    "ChannelCharter",
    "channel_accepts_content",
    "charter_for",
]
