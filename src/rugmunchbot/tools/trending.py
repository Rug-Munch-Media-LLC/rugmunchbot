"""Legacy ``trending()`` entry point — now a pure delegation to the engine.

Kept only because ``channels/daily_market_summary/_fetch.py`` still calls it.
It computes nothing: the ranked list comes from ``rugbrain.trending_fusion/v1``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rugmunchbot.tools.trending_engine import (
    TrendingEngineUnavailable,
    fetch_trending,
    ranked_tokens,
)
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")


async def trending(chain: str = "solana", limit: int = 20) -> list[dict[str, Any]] | None:
    """Return the canonical ranked tokens, or None when the engine is unavailable."""
    try:
        payload = await fetch_trending(chain, limit)
    except TrendingEngineUnavailable as exc:
        logger.info("trending engine unavailable: %s (%s)", exc.source, exc.reason)
        return None
    return ranked_tokens(payload) or None


def _scanner_candidates(chain: str = "solana", limit: int = 20) -> list[dict[str, Any]]:
    """Candidate discovery for content quality — engine candidates, never scored here."""
    try:
        payload = asyncio.run(fetch_trending(chain, limit)) if not asyncio._get_running_loop() else None  # noqa: SLF001
    except (TrendingEngineUnavailable, RuntimeError):
        return []
    return ranked_tokens(payload) if payload else []
