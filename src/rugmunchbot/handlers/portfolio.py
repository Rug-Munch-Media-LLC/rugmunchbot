"""/portfolio — user portfolio (backend) + batch token risk auditing.

With no arguments the command shows the user's saved portfolio from the
backend (``GET /api/v1/portfolio/``): total value, per-token breakdown with
P&L vs avg buy price, and 24h change. With addresses it runs the batch
risk audit (premium feature): per-token breakdown, concentration warnings,
and worst-performer highlights.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from rugmunchbot.formatters import footer_links, sep
from rugmunchbot.handlers._site_links import with_site_link
from rugmunchbot.services.pricing import resolve
from rugmunchbot.support import backend_user
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools import get_registry
from rugmunchbot.tools._client import BackendApiError

if TYPE_CHECKING:
    from telegram import Update

logger = get_logger("rmi_bot.portfolio")


def _e(s: Any) -> str:
    return html.escape(str(s), quote=False)


def _fmt_usd(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    arrow = "📈" if value >= 0 else "📉"
    return f"{arrow} {value:+.1f}%"


async def _show_user_portfolio(update: Update) -> None:
    """No-arg /portfolio: render the backend user portfolio with live prices."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    wait = await update.message.reply_text("📊 Loading your portfolio...")
    try:
        data = await backend_user.get_portfolio(
            user.id, username=user.username, first_name=user.first_name
        )
    except BackendApiError as exc:
        logger.warning("portfolio_backend_error user=%s: %s", user.id, exc)
        await wait.edit_text(f"❌ Could not load your portfolio ({_e(str(exc))[:120]})")
        return

    entries = data.get("entries") or []
    if not entries:
        await wait.edit_text(
            "📭 Your portfolio is empty. Use /add to add tokens.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_site_link("portfolio"),
        )
        return

    total_value = 0.0
    total_cost = 0.0
    valued = 0
    lines: list[str] = [f"📊 <b>Your Portfolio</b> ({len(entries)} tokens)\n{sep()}\n"]
    for entry in entries[:15]:
        symbol = entry.get("symbol") or "???"
        chain = entry.get("chain") or "?"
        address = entry.get("address") or ""
        try:
            amount = float(entry.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        try:
            avg_buy = float(entry.get("avg_buy_price") or 0)
        except (TypeError, ValueError):
            avg_buy = 0.0

        price: float | None = None
        change_24h: float | None = None
        if address:
            pr = await resolve(address=address, chain=chain)
            if pr.price_usd:
                price = pr.price_usd
                change_24h = pr.change_24h

        if price is not None and amount:
            value = amount * price
            total_value += value
            valued += 1
            pnl = (price - avg_buy) * amount if avg_buy else None
            if avg_buy:
                total_cost += amount * avg_buy
            pnl_txt = ""
            if pnl is not None:
                sign = "+" if pnl >= 0 else ""
                pnl_txt = f" · P&L {sign}{_fmt_usd(pnl)}"
            chg_txt = f" · 24h {_fmt_pct(change_24h)}" if change_24h is not None else ""
            addr_txt = f" <code>{address[:10]}…</code>" if address else ""
            lines.append(
                f"• <b>{_e(symbol)}</b> ({_e(chain)}){addr_txt}\n"
                f"  {amount:g} @ {_fmt_usd(price)} = <b>{_fmt_usd(value)}</b>"
                f"{pnl_txt}{chg_txt}"
            )
        else:
            held = f"{amount:g} " if amount else ""
            lines.append(f"• <b>{_e(symbol)}</b> ({_e(chain)}) — {held}no live price")

    summary = f"\n{sep()}\n💰 <b>Total value: {_fmt_usd(total_value)}</b>"
    if total_cost and total_value:
        pnl_total = total_value - total_cost
        pct = (pnl_total / total_cost) * 100
        sign = "+" if pnl_total >= 0 else ""
        summary += f" · P&L {sign}{_fmt_usd(pnl_total)} ({sign}{pct:.1f}%)"
    if valued < len(entries):
        summary += f"\n<i>{len(entries) - valued} token(s) unvalued (no live price)</i>"
    lines.append(summary)
    lines.append(footer_links())

    await wait.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=with_site_link("dashboard"),
        disable_web_page_preview=True,
    )


async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/portfolio — user portfolio; /portfolio <addr...> — batch risk audit."""
    if not update.message:
        return
    addresses = [a.strip() for a in (ctx.args or []) if a.strip()]
    if not addresses:
        await _show_user_portfolio(update)
        return
    if len(addresses) > 10:
        await update.message.reply_text("⚠️ Max 10 tokens per audit.")
        return

    wait = await update.message.reply_text(f"📊 Auditing {len(addresses)} tokens...")
    registry = get_registry()
    result = await registry.run("portfolio_risk", scan_result={"addresses": addresses})

    # The tool expects scan_result dict; call directly if registry path differs
    if result.data and result.data.get("error") and "scan_result" in str(result.data.get("error")):
        from rugmunchbot.tools.portfolio_risk import audit_portfolio

        data = await audit_portfolio(addresses)
    else:
        data = result.data or {}

    if data.get("error"):
        await wait.edit_text(f"❌ {_e(data['error'])}")
        return

    grade = data.get("grade", "?")
    label = data.get("grade_label", "")
    total = data.get("total_tokens", 0)
    risky = data.get("risky_count", 0)
    risky_pct = data.get("risky_pct", 0)

    grade_emoji = {"A": "🟢", "B": "🟢", "C": "🟡", "D": "🟠", "F": "🔴"}.get(grade, "⚪")

    lines = [
        f"{grade_emoji} <b>PORTFOLIO GRADE: {grade}</b> — {_e(label)}",
        f"<i>{total} tokens · {risky}/{total} risky ({risky_pct}%) · "
        f"avg score {data.get('avg_score', '?')}</i>",
        "",
        "<b>Per-Token Breakdown:</b>",
    ]
    for tok in data.get("tokens", []):
        v = tok.get("verdict", "?")
        emoji = {"CLEAN": "✅", "SUSPECT": "⚠️", "LIKELY_WASHED": "🚨"}.get(v, "⚪")
        lines.append(f"  {emoji} {_e(tok['address'][:14])}… — {v} ({tok.get('score', '?')}/100)")

    warning = data.get("concentration_warning")
    if warning:
        lines.extend(["", f"⚠️ <b>{_e(warning)}</b>"])

    failed = data.get("failed_addresses")
    if failed:
        lines.extend(["", f"<i>{len(failed)} addresses failed to scan</i>"])

    lines.extend(["", "via @rugmunchbot · rugmunch.io"])
    await wait.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def register_portfolio(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
