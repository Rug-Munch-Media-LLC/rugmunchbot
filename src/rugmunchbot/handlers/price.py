"""
RugMunchBot -- Price and DEX command handlers.

New commands:
  /price <address> [chain]    — Get token price (by contract address)
  /dex <address> [chain]      — Show DEX trading pairs & liquidity

Both commands share ONE price/quote service
(``services/pricing.py``) instead of each growing its own fallback chain.
They pass the user input as ``address=`` — the command contract is "contract
address", so the address chain (scanner cache → DataBus → DexScreener →
Birdeye) is forced exactly like the legacy handlers did. The Redis
cache-aside (TTL 45s) is preserved below.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from rugmunchbot.config import WEBSITE_URL
from rugmunchbot.formatters import (
    detect_chain,
    footer_links,
)
from rugmunchbot.handlers._dex_cmd import cmd_dex as cmd_dex
from rugmunchbot.handlers._messages import (
    ACTION_TRY_AGAIN,
    failure_card,
    usage_hint,
)
from rugmunchbot.handlers._price_card import render_price_card
from rugmunchbot.keyboards import back_kb
from rugmunchbot.services.pricing import resolve
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.price")

# /price Redis cache — TTL 30-60s. The price snapshot is JSON-serializable
# ({price_usd, change_24h, volume_24h, source}); only positive prices are
# cached so a "no data" result never poisons the key. Fail-open: Redis down
# → live fetch every time.
_PRICE_TTL = 45  # within the 30-60s requirement
_PRICE_KEY_PREFIX = "price:"


def _redis_client() -> Any:
    """Async Redis client (lazy import — fail-open when unavailable)."""
    from rugmunchbot._vendor.redis_client import get_redis_async

    return get_redis_async()


async def _cache_get_price(address: str, chain: str) -> dict[str, Any] | None:
    """Load the cached price snapshot (None on any miss)."""
    r = _redis_client()
    if r is None:
        return None
    try:
        raw = await r.get(f"{_PRICE_KEY_PREFIX}{chain}:{address}")
        if not raw:
            return None
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception:  # fail-open by contract
        logger.debug("price:cache_get_failed address=%.20s", address, exc_info=True)
        return None


async def _cache_set_price(address: str, chain: str, result: dict[str, Any]) -> None:
    """Write a positive price snapshot to Redis. Fail-open."""
    r = _redis_client()
    if r is None:
        return
    try:
        await r.set(
            f"{_PRICE_KEY_PREFIX}{chain}:{address}",
            json.dumps(result, default=str),
            ex=_PRICE_TTL,
        )
    except Exception:  # fail-open by contract
        logger.debug("price:cache_set_failed address=%.20s", address, exc_info=True)


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get token price by contract address.

    Usage: /price <address> [chain]
    Example: /price 0x6982508145454Ce325dDbE47a25d4ec3d2311933
             /price EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v solana
    """
    if not context.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "💰 <b>Token Price</b>\n\n"
            + usage_hint(
                "price",
                "address [chain]",
                "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                examples=("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v solana",),
                extra="💡 <i>Get price data directly by contract address, not symbol.</i>",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    address = context.args[0]
    chain = context.args[1].lower() if len(context.args) > 1 else detect_chain(address)

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"💰 <b>Fetching price...</b>\n<code>{address}</code>",
        parse_mode=ParseMode.HTML,
    )

    try:
        # Cache-aside: same address+chain served from Redis for 45s.
        cached = await _cache_get_price(address, chain)
        if cached is not None:
            result = cached
        else:
            # Shared pricing service (scanner cache → backend DataBus →
            # DexScreener → Birdeye). Only positive snapshots are cached so
            # a "no data" result never poisons the key.
            snapshot = await resolve(address=address, chain=chain)
            result = snapshot.to_dict()
            result["market_cap"] = snapshot.market_cap
            result["fetched_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            if snapshot.ok:
                await _cache_set_price(address, chain, result)

        if result["price_usd"] > 0:
            # Multi-chain card; unknown fields render as "-" + the reason.
            text = render_price_card(
                address=address,
                chain=chain,
                data=result,
                fetched_at=result.get("fetched_at"),
            )
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔍 Full Scan", callback_data=f"scan_{address}_{chain}")],
                    [
                        InlineKeyboardButton(
                            "📊 Charts & Analysis", url=f"{WEBSITE_URL}/markets/token/{address}"
                        )
                    ],
                    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
                ]
            )
        else:
            text = (
                failure_card(
                    "Price Not Available",
                    f"<code>{address}</code>\n\n"
                    "No price data found for this token. It may:\n"
                    "• Not have a trading pair yet\n"
                    "• Be on an unsupported chain\n"
                    "• Have no liquidity",
                    "Try /scan for a full security analysis.",
                )
                + footer_links()
            )
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔍 Full Scan", callback_data=f"scan_{address}_{chain}")],
                    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
                ]
            )

        await msg.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
        )

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, OSError) as e:
        logger.warning("cmd_price failed for %s: %s", address, e)
        await msg.edit_text(
            failure_card(
                "Price Check Failed",
                f"Could not fetch price for <code>{address}</code>.",
                ACTION_TRY_AGAIN,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
