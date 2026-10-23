"""Polymarket referral — NOT configured; never invent a link."""

from __future__ import annotations

import os

from ._tiers import STANDARD
from ._types import Referral

POLYMARKET_PLATFORM = "polymarket"
POLYMARKET_ENV_KEY = "RMI_REF_POLYMARKET"
POLYMARKET_UNCONFIGURED = "polymarket:not-configured"


def polymarket_referral() -> Referral:
    """Resolve the Polymarket referral.

    No referral programme link exists yet, so this returns ``url=None`` with
    an explicit reason: callers render ``null`` + reason, never a fabricated
    link. Ops can enable it later by exporting ``RMI_REF_POLYMARKET`` as a
    full https URL — nothing else changes.
    """
    url = (os.environ.get(POLYMARKET_ENV_KEY) or "").strip()
    if not url:
        return Referral(
            POLYMARKET_PLATFORM, STANDARD, None, None, POLYMARKET_UNCONFIGURED
        )
    if not url.startswith("https://"):
        return Referral(
            POLYMARKET_PLATFORM, STANDARD, None, None, "polymarket:invalid-url"
        )
    return Referral(POLYMARKET_PLATFORM, STANDARD, url, None)
