"""Market selection: pure ranking over a Gamma ``/markets`` payload.

Selection rules (all thresholds configurable):
  * volume and liquidity floors — no thin books;
  * odds-shift filter — ``oneDayPriceChange`` at or above ``min_abs_change``;
  * category filter — event tags where present, keyword routing otherwise
    (:mod:`rugmunchbot.polymarket.categories`);
  * past-end-date markets are skipped (not "pre-resolution");
  * near-resolution bonus — markets ending within ``near_hours`` rank higher.

Nothing is estimated: a market without a real ``volume24hr`` or liquidity
number is dropped rather than scored on a guess.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.polymarket.categories import classify_market
from rugmunchbot.polymarket.models import SelectedMarket

EVENT_URL = "https://polymarket.com/event/{slug}"


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _yes_price(market: dict[str, Any]) -> float | None:
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(prices, list) or not prices:
        return None
    outcomes = market.get("outcomes")
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except (json.JSONDecodeError, TypeError):
            outcomes = None
    if isinstance(outcomes, list):
        for index, name in enumerate(outcomes):
            if str(name).strip().lower() in {"yes", "true"} and index < len(prices):
                return _num(prices[index])
    return _num(prices[0])


def _event_slug(market: dict[str, Any]) -> str:
    events = market.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        slug = str(events[0].get("slug") or "").strip()
        if slug:
            return slug
    return str(market.get("slug") or "").strip()


def _hours_to_end(end_iso: Any, now: datetime) -> float | None:
    if not end_iso or not isinstance(end_iso, str):
        return None
    try:
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return (end - now).total_seconds() / 3600.0


def _score(market: SelectedMarket, near_hours: float | None) -> float:
    score = math.log10(max(market.volume_24h_usd, 1.0)) + 0.5 * math.log10(
        max(market.liquidity_usd, 1.0)
    )
    score += 10.0 * abs(market.change_1d or 0.0)
    if (
        near_hours
        and market.hours_to_resolution is not None
        and 0 <= market.hours_to_resolution <= near_hours
    ):
        score += 0.75
    return score


def select_markets(
    raw_markets: list[dict[str, Any]],
    *,
    now: datetime,
    min_volume_usd: float = 250_000.0,
    min_liquidity_usd: float = 50_000.0,
    min_abs_change_1d: float = 0.0,
    categories: tuple[str, ...] = (),
    near_hours: float | None = 72.0,
    limit: int = 3,
) -> list[SelectedMarket]:
    """Filter and rank open markets. Returns at most ``limit`` entries."""
    picked: list[SelectedMarket] = []
    for row in raw_markets:
        if not isinstance(row, dict) or row.get("closed") or row.get("active") is False:
            continue
        volume_24h = _num(row.get("volume24hr"))
        liquidity = _num(row.get("liquidityNum")) or _num(row.get("liquidity"))
        volume = _num(row.get("volumeNum")) or _num(row.get("volume"))
        change_1d = _num(row.get("oneDayPriceChange"))
        if volume_24h is None or liquidity is None or volume is None:
            continue
        if volume_24h < min_volume_usd or liquidity < min_liquidity_usd:
            continue
        if min_abs_change_1d and abs(change_1d or 0.0) < min_abs_change_1d:
            continue
        category = classify_market(row)
        if categories and category not in categories:
            continue
        condition_id = str(row.get("conditionId") or "").strip()
        question = str(row.get("question") or "").strip()
        slug = _event_slug(row)
        if not condition_id or not question or not slug:
            continue
        hours_left = _hours_to_end(row.get("endDateIso"), now)
        if hours_left is not None and hours_left < 0:
            continue
        picked.append(
            SelectedMarket(
                condition_id=condition_id,
                question=question,
                slug=slug,
                category=category,
                yes_price=_yes_price(row),
                volume_usd=volume,
                volume_24h_usd=volume_24h,
                liquidity_usd=liquidity,
                change_1d=change_1d,
                end_date=str(row.get("endDateIso") or row.get("endDate") or "") or None,
                hours_to_resolution=hours_left,
                url=EVENT_URL.format(slug=slug),
            )
        )
    picked.sort(key=lambda m: _score(m, near_hours), reverse=True)
    return picked[: max(limit, 0)]
