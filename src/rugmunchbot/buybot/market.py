"""Market snapshot adapter over the shared price service.

``services.pricing.get_price`` is the bot's single price fallback chain, so the
popup never invents a number: whatever that service cannot prove stays
``None`` and renders as ``n/a``.
"""

from __future__ import annotations

from rugmunchbot.buybot.render import MarketSnapshot
from rugmunchbot.services.pricing import get_price
from rugmunchbot.support.logging import get_logger

__all__ = ["PricingMarketSource"]

logger = get_logger("rmi_bot.buybot.market")


class PricingMarketSource:
    """``MarketSource`` adapter — never raises, returns ``None`` when all sources fail."""

    async def snapshot(self, chain: str, address: str) -> MarketSnapshot | None:
        try:
            result = await get_price(address, chain)
        except Exception:  # a price outage is a missing row, not a broken card
            logger.warning("buybot_price_failed chain=%s", chain, exc_info=True)
            return None
        if not result.ok:
            return None
        return MarketSnapshot(
            price_usd=result.price_usd or None,
            mcap_usd=result.market_cap,
            liquidity_usd=result.liquidity,
        )
