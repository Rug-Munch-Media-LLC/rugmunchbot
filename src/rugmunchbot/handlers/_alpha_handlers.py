#!/usr/bin/env python3
"""
RugMunchBot -- /alpha premium alpha-signals handler.

/alpha — Premium alpha signals feed, three sections:

  1. Early Token Detection — tokens launched in the last 24h with an
     alpha score (backend /market/launches).
  2. Smart Money Flows — whale moves flagged ``is_smart_money``
     (backend /market/whales).
  3. Alpha Signals — curated feed derived from whale moves, fresh
     high-alpha launches and bundler detections.

Free users see the top 1 from each category plus a paywall; Scout+ see
the full 5 per category (Scout is a paid tier — the teaser converts free
users, paid tiers get the full feed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import fmt_chain
from rugmunchbot.formatters import footer_links, sep, thin_sep
from rugmunchbot.handlers.market import _fetch_market_intel, _fmt_usd
from rugmunchbot.states import is_owner
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot")

__all__ = ["cmd_alpha", "register_alpha"]

_PREMIUM_TIERS = frozenset({"scout", "hunter", "pro"})
_ALPHA_LIMIT = 5


def _alpha_score(item: dict[str, Any]) -> int:
    """Heuristic 0-100 alpha score for a fresh launch.

    Deterministic blend of 24h volume, price momentum and bundler risk.
    Pure function of the backend payload — no network, no randomness.
    """
    try:
        vol = float(item.get("volume_24h") or 0)
    except (TypeError, ValueError):
        vol = 0.0
    try:
        change = float(item.get("change_24h") or 0)
    except (TypeError, ValueError):
        change = 0.0

    score = 40
    if vol >= 1_000_000:
        score += 30
    elif vol >= 100_000:
        score += 20
    elif vol >= 10_000:
        score += 10
    if change >= 100:
        score += 20
    elif change >= 25:
        score += 10
    elif change < -50:
        score -= 15
    if item.get("bundler_detected"):
        score -= 25
    return max(0, min(100, score))


def _risk_label(item: dict[str, Any]) -> str:
    if item.get("bundler_detected"):
        return "HIGH"
    score = _alpha_score(item)
    if score >= 70:
        return "LOW"
    if score >= 45:
        return "MEDIUM"
    return "HIGH"


def _render_early(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for i, item in enumerate(items, 1):
        symbol = (item.get("symbol") or "???").upper()[:12]
        chain_txt = fmt_chain(item.get("chain") or "solana")
        score = _alpha_score(item)
        lines.append(f"{i}. <b>${symbol}</b> ({chain_txt}) — Alpha: <b>{score}/100</b>")
        detail = f"   Vol: {_fmt_usd(item.get('volume_24h'))} | Risk: {_risk_label(item)}"
        if item.get("bundler_detected"):
            detail += " | 🚨 bundler"
        lines.append(detail)
    return lines


def _render_smart(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for i, item in enumerate(items, 1):
        symbol = (item.get("token_symbol") or "???").upper()[:12]
        action = (item.get("type") or "buy").lower()
        verb = "buying" if action == "buy" else "selling"
        emoji = "\U0001f7e2" if action == "buy" else "\U0001f534"
        lines.append(
            f"{i}. {emoji} <b>${symbol}</b> — smart wallet {verb} "
            f"({_fmt_usd(item.get('value_usd'))})"
        )
    return lines


def _render_signals(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for i, (tag, text) in enumerate(items, 1):
        lines.append(f"{i}. <b>[{tag}]</b> {text}")
    return lines


def _build_signals(
    whales: list[dict[str, Any]], launches: list[dict[str, Any]]
) -> list[tuple[str, str]]:
    """Curated alpha signals from whale moves + fresh launches."""
    signals: list[tuple[str, str]] = []
    for w in whales:
        symbol = (w.get("token_symbol") or "???").upper()[:12]
        action = (w.get("type") or "buy").lower()
        verb = "accumulating" if action == "buy" else "likely selling"
        signals.append(
            ("WHALE", f"{_fmt_usd(w.get('value_usd'))} {symbol} move — smart wallets {verb}")
        )
    for item in launches:
        symbol = (item.get("symbol") or "???").upper()[:12]
        score = _alpha_score(item)
        if score >= 70 and not item.get("bundler_detected"):
            signals.append(("EARLY", f"New token ${symbol} with {score} alpha score detected"))
        if item.get("bundler_detected"):
            signals.append(("RISK", f"${symbol} launch shows bundler activity — avoid"))
    return signals[:_ALPHA_LIMIT]


async def cmd_alpha(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/alpha — Premium alpha signals feed (early detection, smart money, curated signals)."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    tier = db.get_user_tier(user.id)
    premium = tier in _PREMIUM_TIERS or is_owner(user.id)
    per_cat = _ALPHA_LIMIT if premium else 1

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f3af <b>Scanning for alpha signals...</b>", parse_mode=ParseMode.HTML
    )

    launches_data, whales_data = (
        await _fetch_market_intel("/api/v1/market/launches", params={"limit": 10}),
        await _fetch_market_intel("/api/v1/market/whales", params={"limit": 10}),
    )
    launches = list((launches_data or {}).get("items") or [])
    whales = list((whales_data or {}).get("items") or [])
    if not launches and not whales:
        await msg.edit_text(
            "\u26a0\ufe0f <b>Alpha Unavailable</b>\n\n"
            "Could not fetch alpha signals right now.\nPlease try again in a moment.",
            parse_mode=ParseMode.HTML,
        )
        return

    smart = [w for w in whales if w.get("is_smart_money")][:per_cat]
    signals = _build_signals(whales, launches)[:per_cat]

    lines = ["\U0001f3af <b>Alpha Insider — Premium Signals</b>", sep(), ""]

    lines.append("\U0001f680 <b>Early Detection</b>")
    lines.append(thin_sep())
    early = launches[:per_cat]
    lines.extend(_render_early(early) or ["<i>No fresh launches right now.</i>"])
    lines.append("")

    lines.append("\U0001f40b <b>Smart Money Flows</b>")
    lines.append(thin_sep())
    lines.extend(_render_smart(smart) or ["<i>No smart money moves detected.</i>"])
    lines.append("")

    lines.append("\U0001f4e1 <b>Alpha Signals</b>")
    lines.append(thin_sep())
    lines.extend(_render_signals(signals) or ["<i>No signals in the current window.</i>"])

    if not premium:
        lines.extend(
            [
                "",
                "\U0001f512 <b>Unlock all 15 signals — upgrade to Premium</b>",
                "<i>Scout+ gets full early detection, smart money flows and curated signals.</i>",
            ]
        )
    lines.append(footer_links())

    rows: list[list[Any]] = []
    if not premium:
        rows.append([InlineKeyboardButton("\u2b50 View Plans", callback_data="menu_pricing")])
    rows.append([InlineKeyboardButton("\U0001f504 Refresh", callback_data="alpha_refresh")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )


def register_alpha(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register /alpha on the bot application (wired from bot/_entry.py)."""
    from telegram.ext import CommandHandler

    app.add_handler(CommandHandler("alpha", cmd_alpha))
