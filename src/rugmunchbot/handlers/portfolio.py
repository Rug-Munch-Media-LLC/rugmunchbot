"""/portfolio — batch token risk auditing (premium feature).

Users send multiple addresses; get a portfolio-level risk grade with
per-token breakdown, concentration warnings, and worst-performer
highlights.
"""

from __future__ import annotations

import html
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from rugmunchbot.tools import get_registry


def _e(s: Any) -> str:
    return html.escape(str(s), quote=False)


async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/portfolio <addr1> <addr2> ... — batch risk audit."""
    if not update.message:
        return
    addresses = [a.strip() for a in (ctx.args or []) if a.strip()]
    if not addresses:
        await update.message.reply_text(
            "📊 <b>Portfolio Risk Auditor</b>\n\n"
            "Usage: <code>/portfolio addr1 addr2 addr3</code>\n"
            "Scans all tokens in parallel and grades your portfolio A-F.\n"
            "Example: <code>/portfolio 0xABC 0xDEF 0x123</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if len(addresses) > 10:
        await update.message.reply_text("⚠️ Max 10 tokens per audit.")
        return

    wait = await update.message.reply_text(
        f"📊 Auditing {len(addresses)} tokens..."
    )
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
        lines.append(
            f"  {emoji} {_e(tok['address'][:14])}… — {v} ({tok.get('score', '?')}/100)"
        )

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
