"""Admin-configurable buy popups (``/buybot``) for any token, in any chat.

Layers, one job per module: :mod:`models` (validated config, community
models reused) · :mod:`store` (Redis, fail-soft reads) · :mod:`ledger`
(dedupe + card cap) · :mod:`render` (pure HTML card) · :mod:`execution`
(injectable swap seam — a preview, never a fake settlement) · :mod:`market`
(shared price service) · :mod:`commands` (Telegram-free grammar) ·
:mod:`callbacks` + :mod:`handlers` (routing, admin-gated).
"""

from rugmunchbot.buybot.execution import BuyExecutor, BuyRequest, ExecutionResult, SwapService
from rugmunchbot.buybot.handlers import register_buybot
from rugmunchbot.buybot.ledger import CardLedger
from rugmunchbot.buybot.models import BuybotConfig
from rugmunchbot.buybot.render import BuyEvent, MarketSnapshot, render_card
from rugmunchbot.buybot.state import BuybotState, get_state
from rugmunchbot.buybot.store import load_config, save_config

__all__ = [
    "BuyEvent",
    "BuyExecutor",
    "BuyRequest",
    "BuybotConfig",
    "BuybotState",
    "CardLedger",
    "ExecutionResult",
    "MarketSnapshot",
    "SwapService",
    "get_state",
    "load_config",
    "register_buybot",
    "render_card",
    "save_config",
]
