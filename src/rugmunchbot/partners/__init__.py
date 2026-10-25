"""Partner affiliate surface — every content lane resolves partners here.

Featured partners (real configured codes only): Jupiter (standard/ultra,
50 bps), GMGN ``rugmunch``, Banana Gun ``rugmunch``, Axiom ``@crmuncher``,
Odin ``x5pyi0``, Padre ``crm``. Hyperliquid (50 bps) has no code stored and
Polymarket is unconfigured, so both resolve to ``url=None`` + ``reason``
rather than an invented link.
"""

from __future__ import annotations

from ._catalog import (
    DEX_PARTNERS,
    MANUAL_PARTNERS,
    display_name,
    referral_for,
    referral_snapshot,
)
from ._gmgn import DEFAULT_GMGN_CODE, GMGN_CHAIN_SLUGS, gmgn_slug, gmgn_url
from ._lanes import (
    ALPHA,
    AUTOMATION_LANES,
    CONTENT_TYPE_LANES,
    LANE_PARTNERS,
    NEWS,
    PARTNER_LANES,
    PREDICTIONS,
    SCAM_ALERT,
    SMART_MONEY,
    WHALES,
    lane_for_content_type,
    normalize_lane,
)
from ._render import STRIP_MARK, partner_strip, render_strip
from ._select import (
    DEFAULT_MAX_LINKS,
    footer_enabled,
    max_links,
    partner_referrals,
    select_partners,
)
from ._strip import with_partner_strip

__all__ = [
    "ALPHA",
    "AUTOMATION_LANES",
    "CONTENT_TYPE_LANES",
    "DEFAULT_GMGN_CODE",
    "DEFAULT_MAX_LINKS",
    "DEX_PARTNERS",
    "GMGN_CHAIN_SLUGS",
    "LANE_PARTNERS",
    "MANUAL_PARTNERS",
    "NEWS",
    "PARTNER_LANES",
    "PREDICTIONS",
    "SCAM_ALERT",
    "SMART_MONEY",
    "STRIP_MARK",
    "WHALES",
    "display_name",
    "footer_enabled",
    "gmgn_slug",
    "gmgn_url",
    "lane_for_content_type",
    "max_links",
    "normalize_lane",
    "partner_referrals",
    "partner_strip",
    "referral_for",
    "referral_snapshot",
    "render_strip",
    "select_partners",
    "with_partner_strip",
]
