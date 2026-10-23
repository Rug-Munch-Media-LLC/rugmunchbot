"""Content-cycle orchestrator: select -> evidence -> card -> dedupe -> (maybe) publish.

Safety model:
  * **Dry run is the default.** ``PolySettings.dry_run`` is True unless
    ``POLYMARKET_DRY_RUN=false`` AND a publisher callback is supplied.
  * The engine never trades, signs, transfers or creates markets. It only calls
    three public read endpoints.
  * A failed market fetch yields no drafts (never a fabricated card). A failed
    wallet fetch renders "value unavailable", never "$0".
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rugmunchbot.polymarket.card import render_content_card
from rugmunchbot.polymarket.dedupe import Dedupe
from rugmunchbot.polymarket.flow import build_flow_evidence
from rugmunchbot.polymarket.http import PolymarketApiError
from rugmunchbot.polymarket.markets import select_markets
from rugmunchbot.polymarket.models import ContentDraft, FlowEvidence, SelectedMarket, WalletState
from rugmunchbot.polymarket.ratelimit import PostRateLimiter
from rugmunchbot.polymarket.wallet import build_wallet_state
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from rugmunchbot.polymarket.client import PolymarketData
    from rugmunchbot.polymarket.config import PolySettings

logger = get_logger("rmi_bot.polymarket.engine")

Publisher = Callable[[ContentDraft], Awaitable[None]]
_MAX_WALLET_LOOKUPS = 8


async def _wallet_state(data: PolymarketData, address: str) -> WalletState:
    try:
        value = await data.wallet_value(address)
        positions = await data.wallet_positions(address)
    except PolymarketApiError as exc:
        logger.warning("polymarket: wallet lookup failed (%s): %s", address, exc)
        return build_wallet_state(address, None, [])
    return build_wallet_state(address, value, positions)


async def _flow(
    data: PolymarketData, market: SelectedMarket, settings: PolySettings
) -> FlowEvidence:
    try:
        raw = await data.trades(
            market.condition_id, min_cash=settings.min_trade_notional_usd, limit=50
        )
    except PolymarketApiError as exc:
        logger.warning("polymarket: trades unavailable for %s: %s", market.condition_id, exc)
        raw = []
    wallets = list(dict.fromkeys(str(r.get("proxyWallet") or "") for r in raw))[
        :_MAX_WALLET_LOOKUPS
    ]
    wallets = [w for w in wallets if w]
    counts = await asyncio.gather(*(data.traded_count(w) for w in wallets))
    traded = dict(zip(wallets, counts, strict=True))
    return build_flow_evidence(
        market.condition_id,
        raw,
        traded,
        min_notional_usd=settings.min_trade_notional_usd,
        low_history_max_markets=settings.low_history_max_markets,
        max_trades=settings.max_flow_trades,
    )


async def build_drafts(
    data: PolymarketData,
    settings: PolySettings,
    *,
    now: datetime | None = None,
    dedupe: Dedupe | None = None,
    limiter: PostRateLimiter | None = None,
    publish: Publisher | None = None,
) -> list[ContentDraft]:
    """Run one content cycle. Returns the drafts produced (published or not)."""
    moment = now or datetime.now(UTC)
    try:
        raw_markets = await data.markets()
    except PolymarketApiError as exc:
        logger.warning("polymarket: market fetch failed, no cards this cycle: %s", exc)
        return []

    selected = select_markets(
        raw_markets,
        now=moment,
        min_volume_usd=settings.min_volume_usd,
        min_liquidity_usd=settings.min_liquidity_usd,
        min_abs_change_1d=settings.min_abs_change_1d,
        categories=settings.categories,
        limit=settings.max_markets,
    )
    if not selected:
        logger.info("polymarket: no market passed the selection filters this cycle")
        return []

    wallet = await _wallet_state(data, settings.our_wallet)
    seen = dedupe if dedupe is not None else Dedupe()
    gate = limiter if limiter is not None else PostRateLimiter(settings.min_post_interval_s)
    drafts: list[ContentDraft] = []

    for market in selected:
        if gate.remaining > 0:
            break
        key = f"pm:{market.condition_id}"
        if not seen.claim(key):
            continue
        if not gate.allow():
            break
        flow = await _flow(data, market, settings)
        draft = ContentDraft(
            condition_id=market.condition_id,
            text=render_content_card(
                market,
                flow,
                wallet,
                generated_at=moment,
                referral_url=settings.referral_url,
                builder_code=settings.builder_code,
            ),
            source_url=market.url,
            dedupe_key=key,
            generated_at=moment.isoformat(),
            dry_run=settings.dry_run or publish is None,
        )
        if publish is not None and not settings.dry_run:
            await publish(draft)
            logger.info("polymarket: published card for %s", market.condition_id)
        else:
            logger.info("polymarket: dry-run card for %s", market.condition_id)
        drafts.append(draft)
    return drafts
