"""Process state for the popup: ledger, swap executor, market source.

Injected through ``Application.bot_data`` so handlers stay testable and no
module-level mutable state leaks between chats (or between tests).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.buybot.execution import BuyExecutor, SwapService
from rugmunchbot.buybot.ledger import CardLedger
from rugmunchbot.buybot.market import PricingMarketSource

if TYPE_CHECKING:
    from rugmunchbot.buybot.models import BuybotConfig
    from rugmunchbot.buybot.render import MarketSnapshot

__all__ = ["STATE_KEY", "BuybotState", "get_state"]

STATE_KEY = "buybot_state"
_UNSET = object()


class BuybotState:
    """Everything the popup needs at runtime, all injectable."""

    def __init__(
        self,
        ledger: CardLedger | None = None,
        executor: BuyExecutor | None = None,
        market: Any = _UNSET,
    ) -> None:
        self.ledger = ledger if ledger is not None else CardLedger()
        self.executor = executor if executor is not None else BuyExecutor()
        self.market = PricingMarketSource() if market is _UNSET else market

    async def snapshot(self, config: BuybotConfig) -> MarketSnapshot | None:
        """Market rows for *config*, or ``None`` when no source is wired."""
        if self.market is None or config.token is None:
            return None
        return await self.market.snapshot(config.chain.value, config.token.address)

    def swap_service(self, swap: SwapService) -> None:
        """Install an operator-provided swap adapter (the only settlement path)."""
        self.executor = BuyExecutor(swap)


def get_state(context: Any) -> BuybotState:
    """Return (and lazily create) the state stored on ``application.bot_data``."""
    store = context.application.bot_data
    if STATE_KEY not in store:
        store[STATE_KEY] = BuybotState()
    return store[STATE_KEY]
