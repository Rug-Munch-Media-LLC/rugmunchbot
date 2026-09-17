"""/whalewatch <address> — premium whale-wallet deep-dive.

Profiles a specific whale wallet: current holdings, recent trades,
win rate / realized PnL, and how to wire live trade alerts. Real data
via the backend wallet routes (holdings + PnL + v2 forensics).
"""

from __future__ import annotations

import contextlib
import html
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

if TYPE_CHECKING:
    from telegram import Update

from rugmunchbot import db
from rugmunchbot.formatters import detect_chain, is_owner, sep
from rugmunchbot.keyboards import paywall_kb
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.security import wallet_analysis

if TYPE_CHECKING:
    from rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.whalewatch")


def _e(s: Any) -> str:
    return html.escape(str(s), quote=False)


def _usd(v: Any) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "N/A"
    if abs(n) >= 1e9:
        return f"${n / 1e9:.2f}B"
    if abs(n) >= 1e6:
        return f"${n / 1e6:.2f}M"
    if abs(n) >= 1e3:
        return f"${n / 1e3:.1f}K"
    return f"${n:,.2f}"


def _pct(v: Any) -> str:
    try:
        return f"{float(v) * 100:.1f}%" if abs(float(v)) <= 1.0 else f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _holdings_lines(tokens: Any) -> list[str]:
    """Top holdings lines from the wallet tokens payload ([] when empty)."""
    if not isinstance(tokens, list):
        return []
    rows: list[str] = []
    for t in tokens[:8]:
        if not isinstance(t, dict):
            continue
        sym = _e((t.get("symbol") or t.get("name") or "?")[:14])
        amt = t.get("amount") or t.get("balance") or ""
        usd = t.get("usd_value") or t.get("value_usd") or t.get("usd")
        line = f"  \u2022 <b>{sym}</b>"
        if amt:
            line += f" \u2014 {amt}"
        if usd:
            line += f" ({_usd(usd)})"
        rows.append(line)
    return rows


def _pnl_lines(pnl: Any) -> list[str]:
    """Win-rate / PnL lines from the wallet pnl payload ([] when absent)."""
    if not isinstance(pnl, dict):
        return []
    rows: list[str] = []
    wr = pnl.get("win_rate")
    if wr is not None:
        rows.append(f"  \U0001f3af Win rate: <b>{_pct(wr)}</b>")
    realized = pnl.get("realized_pnl_usd") or pnl.get("realized_pnl")
    if realized is not None:
        try:
            n = float(realized)
            emoji = "\U0001f7e2" if n >= 0 else "\U0001f534"
            rows.append(f"  {emoji} Realized PnL: <b>{_usd(n)}</b>")
        except (TypeError, ValueError) as e:
            logger.debug("whalewatch:realized_pnl_parse_failed err=%s", e)
    trades = pnl.get("total_trades") or pnl.get("trades") or pnl.get("trade_count")
    if trades is not None:
        rows.append(f"  \U0001f501 Trades tracked: <b>{_e(trades)}</b>")
    for key, label in (("total_pnl_usd", "Total PnL"), ("unrealized_pnl_usd", "Unrealized")):
        val = pnl.get(key)
        if val is not None:
            with contextlib.suppress(TypeError, ValueError):
                rows.append(f"  \U0001f4b0 {label}: <b>{_usd(float(val))}</b>")
    return rows


def _recent_trade_lines(v2: Any) -> list[str]:
    """Recent-trade lines from the v2 wallet forensics payload ([] when absent)."""
    if not isinstance(v2, dict):
        return []
    trades = v2.get("recent_trades") or v2.get("trades") or []
    if not isinstance(trades, list):
        return []
    rows: list[str] = []
    for t in trades[:5]:
        if not isinstance(t, dict):
            continue
        sym = _e((t.get("symbol") or t.get("token") or "?")[:12])
        side = str(t.get("side") or t.get("type") or "trade").upper()
        usd = t.get("value_usd") or t.get("usd_value")
        rows.append(f"  \u2022 {side} <b>{sym}</b>" + (f" \u2014 {_usd(usd)}" if usd else ""))
    return rows


async def cmd_whalewatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/whalewatch <address> [chain] — premium whale-wallet deep-dive."""
    if not update.message:
        return
    user = update.effective_user
    if user is None:
        return

    if not ctx.args:
        await update.message.reply_text(
            "\U0001f40b <b>Whale Watch</b>\n\n"
            "Usage: <code>/whalewatch &lt;address&gt; [chain]</code>\n\n"
            "Deep-dive any whale wallet: current holdings, recent trades, "
            "win rate and realized PnL.\n\n"
            "\U0001f48e <b>Premium feature</b> \u2014 Scout tier or higher required.",
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return

    tier = db.get_user_tier(user.id)
    if tier not in ("scout", "hunter", "pro") and not is_owner(user.id):
        await update.message.reply_text(
            "\U0001f40b <b>Whale Watch</b>\n\n"
            "This feature requires <b>Scout tier</b> or higher.\n\n"
            "\U0001f48e Unlock: full whale-wallet profiles \u2014 holdings, trade history, "
            "win rate, realized PnL and live trade alerts.",
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return

    address = ctx.args[0]
    chain = ctx.args[1].lower() if len(ctx.args) > 1 else detect_chain(address)

    wait = await update.message.reply_text(
        f"\U0001f40b <b>Profiling whale wallet...</b>\n<code>{_e(address)}</code>",
        parse_mode=ParseMode.HTML,
    )

    result: ToolResult = await wallet_analysis(address=address, chain=chain)
    if not result.ok:
        detail = _e((result.error or "no live wallet data")[:160])
        await wait.edit_text(f"\u274c Whale profile unavailable: {detail}")
        return

    payload = result.data or {}
    holdings = _holdings_lines(payload.get("tokens"))
    pnl = _pnl_lines(payload.get("pnl"))
    trades = _recent_trade_lines(payload.get("v2"))

    lines = ["\U0001f40b <b>Whale Wallet Profile</b>", sep()]
    lines.append(f"<code>{_e(address)}</code>")
    if chain:
        lines.append(f"Chain: {_e(chain)}")

    lines.append("")
    lines.append("<b>\U0001f4bc Current Holdings</b>")
    if holdings:
        lines.extend(holdings)
    else:
        lines.append("  <i>No live holdings data.</i>")

    if pnl:
        lines.append("")
        lines.append("<b>\U0001f4c8 Performance</b>")
        lines.extend(pnl)

    if trades:
        lines.append("")
        lines.append("<b>\U0001f504 Recent Trades</b>")
        lines.extend(trades)

    lines.append("")
    lines.append(sep())
    lines.append(
        "\U0001f514 <b>Live alerts:</b> <code>/whale &lt;address&gt;</code> \u2014 get pinged "
        "when this wallet moves &gt;$50K."
    )
    lines.append(
        f'\U0001f50d <a href="https://rugmunch.io/wallet/{html.escape(address, quote=True)}">'
        "Full analysis on rugmunch.io</a>"
    )
    lines.append("via @rugmunchbot \u00b7 rugmunch.io")

    await wait.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def register_whalewatch(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    app.add_handler(CommandHandler("whalewatch", cmd_whalewatch))
