"""Trending merge, dedup, ranking and honesty footnotes.

Pure list-in/list-out functions — no network I/O, no database access.
Split out of ``handlers/_market_helpers.py`` (2026-10-23).
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.handlers._market_helpers._tokens import (
    _freshness_bonus,
    _mover_bonus,
    _normalize_address,
    _window_change,
)


def _merge_trending_sources(
    rugcharts: list[dict[str, Any]] | None,
    dexscreener: list[dict[str, Any]] | None,
    fresh_launches: list[dict[str, Any]] | None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Merge and deduplicate trending tokens — only tokens WITH a real address.

    Paid placements (the DexScreener token-boost leg) are tagged
    ``paid_placement`` so the renderer can disclose them separately; they are
    only tagged when no organic leg carries the same token.

    Degen ranking intent:
      - CoinGecko ``/search/trending`` is NOT merged: it is a CEX search-score
        feed with no contract address, so it would either render as an
        ``[address not verified]`` placeholder or crowd out real meme movers.
      - Fresh launches (``fresh_launches`` — DexScreener token-profiles /
        pump.fun new deploys) and RugCharts carry the highest priority.
      - Tokens without an ``address`` are dropped — never emitted without a
        real contract address.
      - Ranking rewards: source priority + freshness (newly deployed) + mover
        magnitude (|24h %|, both directions) + live volume/liquidity.
    """
    merged: dict[str, dict[str, Any]] = {}

    # Priority: RugCharts (full data) = fresh launches (degen gold) > DexScreener boosts
    for source_tokens, priority, paid_leg in [
        (rugcharts or [], 3, False),
        (fresh_launches or [], 3, False),
        (dexscreener or [], 2, True),
    ]:
        for t in source_tokens:
            addr = (t.get("address") or "").strip()
            if not addr:
                continue  # no real contract address → never surface (no placeholders)
            norm = _normalize_address(addr)
            existing = merged.get(norm)

            # Quality score: has_name (2) + has_symbol (1) + has_volume (1) + has_liquidity (1) + priority (0-3)
            name = (t.get("name") or "").strip()
            symbol = (t.get("symbol") or "").strip()
            has_name = bool(name and name != "Unknown")
            has_symbol = bool(symbol and symbol != "???")
            has_volume = float(t.get("volume_24h") or 0) > 0
            has_liquidity = float(t.get("liquidity_usd") or 0) > 0
            quality = (
                (2 if has_name else 0)
                + (1 if has_symbol else 0)
                + (1 if has_volume else 0)
                + (1 if has_liquidity else 0)
                + priority
            )

            existing_quality = 0
            if existing:
                ename = (existing.get("name") or "").strip()
                esymbol = (existing.get("symbol") or "").strip()
                existing_quality = (
                    (2 if ename and ename != "Unknown" else 0)
                    + (1 if esymbol and esymbol != "???" else 0)
                    + (1 if float(existing.get("volume_24h") or 0) > 0 else 0)
                    + (1 if float(existing.get("liquidity_usd") or 0) > 0 else 0)
                    + existing.get("_priority", 0)
                )

            if not existing or quality > existing_quality:
                merged[norm] = {
                    **t,
                    "_priority": priority,
                    "_quality": quality,
                    "_source": t.get("source", "unknown"),
                    "_paid_leg": paid_leg,
                }

    # Convert to list and sort by: quality + freshness + mover magnitude, then
    # |24h change| (both directions), then volume.
    def _rank_key(x: dict[str, Any]) -> tuple[float, float, float]:
        return (
            float(x.get("_quality", 0)) + _freshness_bonus(x) + _mover_bonus(x),
            abs(_window_change(x, "24h")),
            float(x.get("volume_24h") or 0),
        )

    # A token carried by an organic leg stays organic even when it is also
    # boosted: paying must not demote (or promote) a real trending entry.
    organic_addrs = {
        _normalize_address((t.get("address") or "").strip())
        for t in [*(rugcharts or []), *(fresh_launches or [])]
        if (t.get("address") or "").strip()
    }
    for norm, row in merged.items():
        row["paid_placement"] = bool(row.get("_paid_leg")) and norm not in organic_addrs

    result = list(merged.values())
    result.sort(key=_rank_key, reverse=True)

    # Filter out tokens with "Unknown" names (prefer named tokens from DexScreener)
    result = [
        t
        for t in result
        if (t.get("name") or "").strip() and (t.get("name") or "").strip() != "Unknown"
    ]

    return result[:limit]


def _sort_trending(tokens: list[dict[str, Any]], window: str) -> list[dict[str, Any]]:
    """Rank trending tokens for the requested window (mutation-free).

    Degen ranking: rank by |change| for the window so the biggest movers in
    BOTH directions (green pumps AND red rugs) surface — a -90% dump is
    exactly as interesting to a degen as a +500% pump. The 1h window has no
    reliable hourly deltas from our sources, so it ranks by 24h volume.
    """
    if window == "1h":
        return sorted(tokens, key=lambda t: float(t.get("volume_24h") or 0), reverse=True)
    if window == "7d" and any(t.get("change_7d") is not None for t in tokens):
        return sorted(tokens, key=lambda t: abs(float(t.get("change_7d") or 0)), reverse=True)
    return sorted(tokens, key=lambda t: abs(float(t.get("change_24h") or 0)), reverse=True)


def _window_note(window: str, tokens: list[dict[str, Any]]) -> str:
    """Honest footnote when the window is backed by fallback data."""
    if window == "1h":
        return "🕒 1h window — ranked by volume (hourly deltas pending)"
    if window == "7d" and not any(t.get("change_7d") is not None for t in tokens):
        return "📅 7d window — using 24h deltas (7d series pending)"
    return ""
