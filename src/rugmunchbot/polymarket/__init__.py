"""Polymarket content/referral engine — read-only, dry-run by default.

Public entry points::

    from rugmunchbot.polymarket import PolySettings, PolymarketData, build_drafts

    drafts = await build_drafts(PolymarketData(), PolySettings.from_env())

Referral/builder facts live in ``docs/POLYMARKET-REFERRAL.md``; the engine
never creates a referral link and never trades.
"""

from __future__ import annotations

from rugmunchbot.polymarket.card import render_content_card
from rugmunchbot.polymarket.client import PolymarketData
from rugmunchbot.polymarket.config import OUR_WALLET, PolySettings
from rugmunchbot.polymarket.dedupe import Dedupe
from rugmunchbot.polymarket.engine import build_drafts
from rugmunchbot.polymarket.flow import build_flow_evidence
from rugmunchbot.polymarket.http import PolymarketApiError
from rugmunchbot.polymarket.markets import select_markets
from rugmunchbot.polymarket.models import (
    ContentDraft,
    FlowEvidence,
    FlowTrade,
    SelectedMarket,
    WalletState,
)
from rugmunchbot.polymarket.ratelimit import PostRateLimiter
from rugmunchbot.polymarket.wallet import build_wallet_state

__all__ = [
    "OUR_WALLET",
    "ContentDraft",
    "Dedupe",
    "FlowEvidence",
    "FlowTrade",
    "PolySettings",
    "PolymarketApiError",
    "PolymarketData",
    "PostRateLimiter",
    "SelectedMarket",
    "WalletState",
    "build_drafts",
    "build_flow_evidence",
    "build_wallet_state",
    "render_content_card",
    "select_markets",
]
