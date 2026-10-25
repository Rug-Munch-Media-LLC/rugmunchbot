"""market /whales — market-wide whale flow analysis (tier-gated)."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import fmt_chain
from rugmunchbot.formatters import footer_links, is_owner, paywall_text, sep, thin_sep
from rugmunchbot.keyboards import token_buttons

from ._constants import _FREE_WHALE_MOVES, MARKET_UNAVAILABLE
from ._data import _fetch_market_intel
from ._render import _fmt_usd, _whale_analysis_bits, _whale_flow_summary

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


async def cmd_whales(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /whales — deep whale analysis: top moves, funding sources,
    wallet ages, historical patterns, net-flow summary and confidence.

    [REVIEW 2026-09-07] Backend also exposes per-token
    ``GET /api/v1/whales/flow`` and ``GET /api/v1/whales/signals`` (both
    require ``address`` + ``chain`` and return a SINGLE token's net-flow /
    rendered signal). This command renders a MARKET-WIDE list of top whale
    moves (``data["items"]``), so those per-token endpoints do NOT cover this
    shape — switching here would break the feed. They are the right source
    for a per-token whale panel (e.g. a scan/token-detail view), not for the
    market-wide /whales list. Left on /api/v1/market/whales.
    """
    msg = await update.message.reply_text(
        "\U0001f40b <b>Deep-scanning whale flow (24h)...</b>", parse_mode=ParseMode.HTML
    )  # type: ignore[union-attr]  # PTB None-able fields
    data = await _fetch_market_intel("/api/v1/market/whales", params={"limit": 10})
    items = (data or {}).get("items") or []
    if not items:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    user = update.effective_user
    tier = db.get_user_tier(user.id) if user else "free"
    premium = tier in ("scout", "hunter", "pro") or (user is not None and is_owner(user.id))

    summary = _whale_flow_summary(items)
    shown = items if premium else items[:_FREE_WHALE_MOVES]

    lines = ["\U0001f40b <b>Whale Flow \u2014 Deep Analysis (24h)</b>", sep()]
    lines.append("<b>\U0001f4ca Whale Summary</b>")
    lines.append(
        f"   \U0001f7e2 Buy volume: <b>{_fmt_usd(summary['buy_vol'])}</b>"
        f" ({summary['buys']} moves) \u00b7 \U0001f534 Sell volume: <b>{_fmt_usd(summary['sell_vol'])}</b>"
        f" ({summary['sells']} moves)"
    )
    net = summary["net"]
    net_txt = f"+{_fmt_usd(net)}" if net >= 0 else f"-{_fmt_usd(abs(net))}"
    ratio = summary["ratio"]
    ratio_txt = f"{ratio:.2f}:1" if ratio is not None else "all buys"
    lines.append(
        f"   \U0001f4b0 Net flow: <b>{net_txt}</b> \u00b7 Buy/sell ratio: <b>{ratio_txt}</b>"
    )
    lines.append(f"   \U0001f3af Whale Confidence: <b>{summary['verdict']}</b>")
    lines.append("")

    lines.append(f"<b>\U0001f40b Top Whale Moves</b> ({len(shown)}/{len(items)})")
    lines.append(thin_sep())
    for i, item in enumerate(shown, 1):
        symbol = (item.get("token_symbol") or "???").upper()[:12]
        action = (item.get("type") or "buy").lower()
        action_txt = "BUY" if action == "buy" else "SELL"
        action_emoji = "\U0001f7e2" if action == "buy" else "\U0001f534"
        chain_raw = item.get("chain") or ""
        chain_txt = f" {fmt_chain(chain_raw)}" if chain_raw else ""
        addr = item.get("token_address") or ""
        wallet = (
            item.get("wallet") or item.get("from") or item.get("to") or item.get("exchange") or ""
        )
        amount = item.get("amount")
        amount_txt = f" \u00b7 {amount}" if amount else ""
        wallet_txt = f" | \U0001f3e6 {html.escape(str(wallet)[:24])}" if wallet else ""
        lines.append(
            f"{i}. {action_emoji} <b>{action_txt} {symbol}</b>{chain_txt}\n"
            f"   <b>{_fmt_usd(item.get('value_usd'))}</b>{amount_txt}"
            f"{wallet_txt}"
            f"{f' | <code>{addr}</code>' if addr else ''}"
        )
        if premium:
            bits = _whale_analysis_bits(item)
            if bits:
                joined = " \u00b7 ".join(bits)
                lines.append(f"   \U0001f9e0 {joined}")
        if i < len(shown):
            lines.append("")

    if not premium:
        locked = len(items) - _FREE_WHALE_MOVES
        lines.append("")
        lines.append(sep())
        lines.append(
            f"\U0001f512 <b>{locked} more whale moves locked</b> \u2014 plus funding sources, "
            "wallet ages and accumulator/dumper history for every whale."
        )
        lines.append(paywall_text(user.id, "whales") if user else paywall_text(0, "scan"))

    lines.append(footer_links())
    rows: list[list[InlineKeyboardButton]] = []
    for item in shown[:5]:
        addr = item.get("token_address") or ""
        if addr:
            rows.append(token_buttons(addr, item.get("chain") or "solana"))
    if not premium:
        rows.append(
            [
                InlineKeyboardButton(
                    "\U0001f48e Unlock Full Whale Analysis", callback_data="menu_pricing"
                )
            ]
        )
    rows.append([InlineKeyboardButton("\U0001f504 Refresh", callback_data="whales_refresh")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )
