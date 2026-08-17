#!/usr/bin/env python3
"""
RugMunchBot -- DeFi market depth handlers.

Commands:
  /yield [stable]         Top yield opportunities (DefiLlama yields API, 5-min cache)
  /lpcheck <address> [chain]  Liquidity pool health check (backend lp-health route)
  /defi                   DeFi overview: global TVL, top chains, top protocols

Data sources:
  * /yield  + /defi  -> DefiLlama public APIs (no auth): yields.llama.fi/pools,
                        api.llama.fi/v2/chains, api.llama.fi/protocols
  * /lpcheck          -> backend /api/v1/lp-health/check/{lp_address} (DataBus-backed)

The bot caches DefiLlama payloads in memory for 5 minutes (the backend does not
expose yield data over a free bot-facing route, so we call DefiLlama directly).
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    backend_headers,
    fmt_chain,
    fmt_number,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.formatters import (
    detect_chain,
    footer_links,
    sep,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.handlers._messages import (
    ACTION_CHECK_ADDRESS,
    ACTION_TRY_AGAIN,
    failure_card,
    usage_hint,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client, http_get_any

if TYPE_CHECKING:
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot.defi")

# ── DefiLlama endpoints (free, no auth) ────────────────────────────────
YIELDS_URL = "https://yields.llama.fi/pools"
CHAINS_URL = "https://api.llama.fi/v2/chains"
PROTOCOLS_URL = "https://api.llama.fi/protocols"

# In-memory TTL cache: {key: (expires_at, payload)}
_CACHE_TTL = 300  # 5 minutes
_cache: dict[str, tuple[float, Any]] = {}

# CEX categories to exclude from the DeFi protocol ranking.
_CEX_CATEGORIES = {"CEX", "Exchange"}


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if not entry:
        return None
    expires_at, payload = entry
    if time.monotonic() > expires_at:
        _cache.pop(key, None)
        return None
    return payload


def _cache_set(key: str, payload: Any) -> None:
    _cache[key] = (time.monotonic() + _CACHE_TTL, payload)


async def _fetch_json(url: str, *, cache_key: str | None = None, timeout_secs: float = 12) -> Any | None:
    """GET a JSON body with a TTL cache. Returns None on any failure."""
    if cache_key:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
    payload = await http_get_any(url, headers={"Accept": "application/json"}, timeout=timeout_secs)
    if payload is None:
        logger.debug("DefiLlama %s failed", url)
        return None
    if cache_key:
        _cache_set(cache_key, payload)
    return payload


# ── /yield: top yield opportunities ────────────────────────────────────

_YIELD_MIN_APY = 5.0
_YIELD_MIN_TVL = 1_000_000


def _yield_candidates(
    pools: list[dict[str, Any]], *, stable_only: bool = False, limit: int = 8
) -> list[dict[str, Any]]:
    """Rank pools by APY, filtering out scam outliers and dust pools.

    Guards: reject outlier-flagged pools, pools below a sane APY floor,
    pools with negligible TVL, and >200% APY (classic exit-liquidity bait).
    """
    candidates = []
    for p in pools:
        if p.get("outlier"):
            continue
        apy = float(p.get("apy") or 0)
        tvl = float(p.get("tvlUsd") or 0)
        if apy < _YIELD_MIN_APY or apy > 200:
            continue
        if tvl < _YIELD_MIN_TVL:
            continue
        if stable_only and not p.get("stablecoin"):
            continue
        candidates.append(p)
    candidates.sort(key=lambda p: float(p.get("apy") or 0), reverse=True)
    return candidates[:limit]


def _render_yield_text(pools: list[dict[str, Any]], *, stable_only: bool) -> str:
    lines = [
        "\U0001f4b0 <b>Top Yield Opportunities</b>",
        f"<i>{'Stablecoin pools' if stable_only else 'All pools'} \u00b7 DefiLlama</i>",
        sep(),
    ]
    for i, p in enumerate(pools, 1):
        project = (p.get("project") or "?").replace("_", " ").title()[:20]
        symbol = (p.get("symbol") or "?")[:16]
        chain = fmt_chain(p.get("chain") or "")
        apy = float(p.get("apy") or 0)
        tvl = float(p.get("tvlUsd") or 0)
        stbl = " \U0001f7e2 stable" if p.get("stablecoin") else ""
        il = " \u26a0\ufe0f IL" if p.get("ilRisk") == "yes" else ""
        lines.append(
            f"{i}. \U0001f7e2 <b>{project}</b> \u00b7 {symbol}{stbl}\n"
            f"   {chain} | <b>{fmt_pct(apy)} APY</b> | TVL {fmt_number(tvl)}{il}"
        )
    lines.append("")
    lines.append("<i>APY > 200% filtered as exit-liquidity bait. Always /scan before depositing.</i>")
    lines.append(footer_links())
    return "\n".join(lines)


async def cmd_yield(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /yield [stable] — top yield opportunities from DefiLlama."""
    stable_only = bool(ctx.args and ctx.args[0].lower() in ("stable", "stablecoin"))
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f4b0 <b>Fetching yield opportunities...</b>", parse_mode=ParseMode.HTML
    )

    payload = await _fetch_json(YIELDS_URL, cache_key="defi_yields")
    if not payload or not isinstance(payload, dict):
        await msg.edit_text(
            "\u26a0\ufe0f <b>Yield Data Unavailable</b>\n\nDefiLlama is not reachable right now.\nPlease try again in a moment.",
            parse_mode=ParseMode.HTML,
        )
        return

    pools = _yield_candidates(payload.get("data") or [], stable_only=stable_only)
    if not pools:
        await msg.edit_text(
            "\u26a0\ufe0f <b>No Qualified Yields</b>\n\nNo pools cleared the risk filters right now.\nPlease try again in a moment.",
            parse_mode=ParseMode.HTML,
        )
        return

    await msg.edit_text(
        _render_yield_text(pools, stable_only=stable_only),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")]]),
        disable_web_page_preview=True,
    )


# ── /lpcheck: liquidity pool health via backend lp-health route ────────

CHAIN_ALIASES = {
    "sol": "solana",
    "eth": "ethereum",
    "bnb": "bsc",
    "arb": "arbitrum",
    "base": "base",
    "polygon": "polygon",
    "tron": "tron",
}


def _resolve_chain(arg: str | None, address: str) -> str:
    if arg:
        return CHAIN_ALIASES.get(arg.lower().strip(), arg.lower().strip())
    detected = detect_chain(address)
    return detected if detected != "unknown" else "ethereum"


def _health_emoji(score: float) -> str:
    if score >= 80:
        return "\U0001f7e2"
    if score >= 60:
        return "\U0001f7e1"
    return "\U0001f534"


def _render_lpcheck_text(health: dict[str, Any]) -> str:
    lines = ["\U0001f6e1\ufe0f <b>Liquidity Pool Health</b>", sep()]
    pair = health.get("token_pair") or health.get("pair") or "?"
    lines.append(f"\U0001f4e6 Pair: <b>{pair}</b>")
    lines.append(f"\U0001f3d8\ufe0f Chain: {fmt_chain(health.get('chain') or 'ethereum')}")
    lines.append("")
    score = float(health.get("health_score") or 0)
    lines.append(f"{_health_emoji(score)} Health Score: <b>{score:.0f}/100</b>")
    lines.append(thin_sep())
    lines.append(f"\U0001f4b0 TVL: <b>{fmt_number(health.get('total_liquidity_usd'))}</b>")
    chg = health.get("liquidity_change_24h")
    if chg is not None:
        lines.append(f"\U0001f4c9 24h Liquidity: <b>{fmt_pct(chg)}</b>")
    lines.append(f"\U0001f465 Largest Holder: <b>{float(health.get('largest_holder_pct') or 0):.1f}%</b>")
    lines.append(f"\U0001f3e2 LP Locked: <b>{float(health.get('lp_locked_pct') or 0):.1f}%</b>")

    alerts = health.get("alerts") or []
    if alerts:
        lines.append("")
        lines.append("\U0001f6a8 <b>Alerts</b>")
        lines.append(thin_sep())
        for a in alerts[:5]:
            lines.append(f"\u2022 {a}")

    lines.append(footer_links())
    return "\n".join(lines)


async def cmd_lpcheck(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /lpcheck <address> [chain] — LP health from the backend route."""
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f6e1\ufe0f <b>LP Health Check</b>\n\n"
            + usage_hint(
                "lpcheck",
                "address [chain]",
                "0x0d4a11d5EEaaC28EC3F61d100daF4d40471f1852",
                examples=("So11111111111111111111111111111111111111112 solana",),
                extra="\U0001f4a1 <i>Checks lock %, holder concentration and 24h liquidity change.</i>",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    address = ctx.args[0].strip()
    chain = _resolve_chain(ctx.args[1] if len(ctx.args) > 1 else None, address)
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f6e1\ufe0f <b>Checking LP health...</b>\n<code>{address}</code>",
        parse_mode=ParseMode.HTML,
    )

    try:
        client = get_shared_client(12)
        r = await client.get(
            f"{BACKEND_URL}/api/v1/lp-health/check/{address}",
            params={"chain": chain},
            headers=backend_headers(),
        )
        if r.status_code != 200:
            await msg.edit_text(
                failure_card(
                    "LP Check Unavailable",
                    "The backend could not verify this pool right now.",
                    ACTION_TRY_AGAIN,
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        health = r.json()
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("lp-health %s failed: %s", address, exc)
        await msg.edit_text(
            failure_card(
                "LP Check Unavailable",
                "The backend is not reachable right now.",
                ACTION_TRY_AGAIN,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    if not isinstance(health, dict) or health.get("error"):
        detail = health.get("error") if isinstance(health, dict) else ""
        await msg.edit_text(
            failure_card(
                "LP Not Found",
                f"No pool data for <code>{address}</code> on {chain.capitalize()}."
                + (f"\n<i>{detail}</i>" if detail else ""),
                ACTION_CHECK_ADDRESS,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    await msg.edit_text(
        _render_lpcheck_text(health),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")]]),
        disable_web_page_preview=True,
    )


# ── /defi: DeFi overview (TVL, chains, protocols) ──────────────────────


def _render_defi_text(chains: list[dict[str, Any]], protocols: list[dict[str, Any]]) -> str:
    lines = [
        "\U0001f3e6 <b>DeFi Overview</b>",
        f"<i>DefiLlama \u00b7 {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC</i>",
        sep(),
    ]

    global_tvl = sum(float(c.get("tvl") or 0) for c in chains)
    lines.append(f"\U0001f310 Global DeFi TVL: <b>{fmt_number(global_tvl)}</b>")

    lines.append("")
    lines.append("\U0001f3ec <b>Top Chains by TVL</b>")
    lines.append(thin_sep())
    for i, c in enumerate(chains[:5], 1):
        chg = c.get("change_1d")
        chg_txt = f" ({fmt_pct(chg)})" if chg is not None else ""
        lines.append(f"{i}. {fmt_chain(c.get('name') or '?')} \u00b7 <b>{fmt_number(c.get('tvl'))}</b>{chg_txt}")

    lines.append("")
    lines.append("\U0001f3e1 <b>Top Protocols by TVL</b>")
    lines.append(thin_sep())
    for i, p in enumerate(protocols[:8], 1):
        cat = (p.get("category") or "DeFi")[:18]
        chg = p.get("change_1d")
        chg_txt = f" ({fmt_pct(chg)})" if chg is not None else ""
        lines.append(f"{i}. <b>{p.get('name')}</b> \u00b7 {cat}\n   {fmt_number(p.get('tvl'))}{chg_txt}")

    lines.append("\n<i>Data: DefiLlama (free API). CEX balances excluded from protocol ranking.</i>")
    lines.append(footer_links())
    return "\n".join(lines)


def _rank_protocols(protocols: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Top DeFi protocols by TVL, excluding centralized exchanges."""
    defi = [p for p in protocols if (p.get("category") or "") not in _CEX_CATEGORIES]
    return sorted(defi, key=lambda p: float(p.get("tvl") or 0), reverse=True)[:limit]


async def cmd_defi(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /defi — DeFi overview from DefiLlama."""
    msg = await update.message.reply_text("\U0001f3e6 <b>Fetching DeFi overview...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields

    chains, protocols = await asyncio.gather(
        _fetch_json(CHAINS_URL, cache_key="defi_chains"),
        _fetch_json(PROTOCOLS_URL, cache_key="defi_protocols"),
    )
    if not isinstance(chains, list) or not isinstance(protocols, list) or not chains:
        await msg.edit_text(
            "\u26a0\ufe0f <b>DeFi Overview Unavailable</b>\n\nDefiLlama is not reachable right now.\nPlease try again in a moment.",
            parse_mode=ParseMode.HTML,
        )
        return

    top_chains = sorted(chains, key=lambda c: float(c.get("tvl") or 0), reverse=True)
    top_protocols = _rank_protocols(protocols)

    await msg.edit_text(
        _render_defi_text(top_chains, top_protocols),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")]]),
        disable_web_page_preview=True,
    )


def register_defi(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register DeFi market-depth commands on the bot application.

    Wires /yield, /lpcheck and /defi in one call. The Lead wires this from
    bot.py (e.g. ``register_defi(app)`` next to ``register_market(app)``).
    """
    app.add_handler(CommandHandler("yield", cmd_yield))
    app.add_handler(CommandHandler("lpcheck", cmd_lpcheck))
    app.add_handler(CommandHandler("defi", cmd_defi))
