"""Inline callback router for the popup buttons.

Payloads are deterministic and tiny: ``buy|<chat>|<mint_ref>|<amount>`` and
``tok|<action>|<chat>|<mint_ref>``. Every tap is re-validated against the
*current* config, so a stale button can never act on a different token, and
each tap is claim-gated, so a double tap executes once.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from rugmunchbot.buybot.execution import BuyRequest
from rugmunchbot.buybot.render import mint_ref
from rugmunchbot.buybot.state import get_state
from rugmunchbot.buybot.store import bot_client, load_config
from rugmunchbot.support.logging import get_logger

__all__ = ["CALLBACK_PATTERN", "on_buy_callback"]

if TYPE_CHECKING:
    from rugmunchbot.buybot.models import BuybotConfig

logger = get_logger("rmi_bot.buybot.callbacks")
CALLBACK_PATTERN = r"^(buy|tok)\|"
TAP_WINDOW_MIN = 15
HINTS = {"chart": "/chart", "scan": "/scan", "holders": "/holders"}
STALE = "This button is stale — run /buybot for fresh buttons."


async def _answer(query: Any, text: str, alert: bool = False) -> None:
    """Answer the callback; a Telegram error must not escape the handler."""
    try:
        await query.answer(text[:190], show_alert=alert)
    except Exception:
        logger.warning("buybot_callback_answer_failed", exc_info=True)


def _parts(data: str | None) -> list[str] | None:
    parts = (data or "").split("|")
    return parts if len(parts) == 4 else None


def _stale_reason(config: BuybotConfig, ref: str) -> str | None:
    if config.token is None or not config.enabled:
        return "The buy panel is closed in this chat."
    if mint_ref(config.token.address) != ref:
        return "The token changed — run /buybot for fresh buttons."
    return None


async def _buy(query: Any, context: Any, config: BuybotConfig, chat_id: int, raw: str) -> None:
    """Validate the amount, claim the tap, then hand it to the swap executor."""
    user_id = int(getattr(getattr(query, "from_user", None), "id", 0) or 0)
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        await _answer(query, STALE)
        return
    if not any(math.isclose(amount, preset, rel_tol=1e-6) for preset in config.presets):
        await _answer(query, "That amount is no longer offered — run /buybot.", alert=True)
        return
    token = config.token
    if token is None:  # guarded by _stale_reason; keeps the type checker honest
        await _answer(query, STALE)
        return
    state = get_state(context)
    key = f"tap:{chat_id}:{user_id}:{token.address}:{amount:g}"
    if not state.ledger.claim(key, max(TAP_WINDOW_MIN, config.dedupe_window_s)):
        await _answer(query, "Already submitted — the tap was not repeated.")
        return
    result = await state.executor.buy(
        BuyRequest(
            chat_id=chat_id,
            user_id=user_id,
            chain=config.chain.value,
            token=token.address,
            amount=amount,
            idempotency_key=f"buybot:{key}",
        )
    )
    if result.ok:
        await _answer(query, f"Submitted ✓ {str(result.tx)[:12]}")
    elif result.status == "preview":
        await _answer(query, f"Preview only — {result.reason}", alert=True)
    else:
        await _answer(query, f"Not executed — {result.reason}", alert=True)


async def on_buy_callback(update: Any, context: Any) -> None:
    """Route ``buy|…`` and ``tok|…`` taps for this chat's popup."""
    query = getattr(update, "callback_query", None)
    if query is None:
        return
    parts = _parts(getattr(query, "data", None))
    if parts is None:
        await _answer(query, STALE)
        return
    kind, chat_raw, ref, value = parts
    chat_id = getattr(getattr(getattr(query, "message", None), "chat", None), "id", None)
    if chat_id is None or str(chat_id) != chat_raw:
        await _answer(query, STALE)
        return
    config = await load_config(chat_id, bot_client(context))
    problem = _stale_reason(config, ref)
    if problem is not None:
        await _answer(query, problem, alert=True)
        return
    if kind == "tok":
        hint = HINTS.get(value)
        address = config.token.address  # non-None: _stale_reason passed
        await _answer(query, f"{hint} {address}" if hint else "Unknown action.", alert=hint is None)
        return
    await _buy(query, context, config, int(chat_id), value)
