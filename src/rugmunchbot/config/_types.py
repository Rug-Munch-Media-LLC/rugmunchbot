#!/usr/bin/env python3
"""TypedDict definitions shared across the config package."""

from __future__ import annotations

from typing import TypedDict


class TierDefinition(TypedDict, total=False):
    name: str
    emoji: str
    scans_per_week: int
    ai_msgs_per_week: int
    price_monthly: int | float
    price_stars: int
    features: list[str]


class StarsPack(TypedDict):
    name: str
    emoji: str
    stars: int


class TopUpPack(TypedDict):
    name: str
    emoji: str
    type: str
    amount: int
    stars: int


class ScanPack(TypedDict):
    name: str
    description: str
    includes: list[str]
    stars: int


class DexRefLink(TypedDict):
    """One referral trade-button entry in ``DEX_REF_LINKS``.

    ``url`` is a template with ``{token}`` / ``{chain}`` / ``{chain_id}``
    placeholders filled at render time by ``formatters/_base.py``.
    """

    name: str
    emoji: str
    url: str
    chains: list[str]
    description: str
