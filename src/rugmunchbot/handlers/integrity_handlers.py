"""Volume-integrity & Dune commands: /volume, /exchangeaudit, /dune.

Thin Telegram render layer over the tool registry — tools own the analysis,
handlers own presentation. Fail-open with honest error cards.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

if TYPE_CHECKING:
    from telegram import Update

from rugmunchbot.tools import get_registry

_VERDICT_EMOJI = {"CLEAN": "✅", "SUSPECT": "⚠️", "LIKELY_WASHED": "🚨",
                  "TRUSTED": "✅", "WASH_RISK": "🚨"}


def _e(s: Any) -> str:
    return html.escape(str(s), quote=False)


async def _run_tool(name: str, **kwargs: Any) -> Any:
    registry = get_registry()
    return await registry.run(name, **kwargs)


async def cmd_volume(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/volume <address> — token fake-volume / wash detection."""
    if not update.message:
        return
    addr = (ctx.args or [""])[0].strip() if ctx.args else ""
    if not addr:
        await update.message.reply_text(
            "Usage: <code>/volume &lt;token address&gt;</code>\n"
            "Runs 5 wash-trading heuristics over live pair data.",
            parse_mode=ParseMode.HTML,
        )
        return
    wait = await update.message.reply_text("🔍 Analyzing volume integrity...")
    result = await _run_tool("volume_integrity", address=addr)
    d = result.data or {}
    if not result.ok:
        await wait.edit_text(f"❌ {_e(d.get('error', 'analysis failed'))}")
        return
    emoji = _VERDICT_EMOJI.get(d.get("verdict", ""), "•")
    lines = [
        f"{emoji} <b>VOLUME INTEGRITY: {d.get('verdict')}</b> — score {d.get('score')}/100",
        f"<i>{_e(d.get('pair', ''))} · liq ${d.get('liquidity_usd') or 0:,.0f} · vol24 ${d.get('volume24_usd') or 0:,.0f}</i>",
        "",
    ]
    pairs_n = d.get("pairs_analyzed")
    if pairs_n:
        lines.append(f"<i>Sampled {pairs_n} pair markets</i>")
        lines.append("")
    for c in d.get("checks", []):
        mark = {"OK": "·", "WARN": "⚠", "CRITICAL": "🚨"}.get(c.get("flag", ""), "·")
        lines.append(f"{mark} <b>{_e(c['check'])}</b> {_e(c.get('value', ''))} — {_e(c.get('note', ''))}")
    lines.append("\nvia @rugmunchbot · full intel: rugmunch.io")
    await wait.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_exchangeaudit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/exchangeaudit <coingecko id|symbol> — venue wash-trading ranking."""
    if not update.message:
        return
    tid = (ctx.args or [""])[0].strip().lower() if ctx.args else ""
    if not tid:
        await update.message.reply_text(
            "Usage: <code>/exchangeaudit &lt;coingecko-id-or-symbol&gt;</code>\n"
            "Ranks every venue trading the token by wash-trading risk.",
            parse_mode=ParseMode.HTML,
        )
        return
    wait = await update.message.reply_text("🔍 Auditing venues...")
    result = await _run_tool("exchange_volume_audit", token_id=tid)
    d = result.data or {}
    if not result.ok:
        await wait.edit_text(f"❌ {_e(d.get('error', 'audit failed'))}")
        return
    lines = [f"<b>EXCHANGE VOLUME AUDIT</b> — {_e(d.get('headline', ''))}", ""]
    for v in d.get("venues", [])[:8]:
        emoji = _VERDICT_EMOJI.get(v["verdict"], "•")
        lines.append(
            f"{emoji} <b>{_e(v['exchange'])}</b> {v['score']}/100 · "
            f"${v['vol_usd_24h']:,.0f} ({v['vol_share']}%)"
        )
        for n in v.get("notes", [])[:2]:
            lines.append(f"   └ {_e(n)}")
    lines.append("\nvia @rugmunchbot · full intel: rugmunch.io")
    await wait.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_dune(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/dune — curated on-chain analytics digest."""
    if not update.message:
        return
    wait = await update.message.reply_text("📊 Pulling Dune analytics...")
    result = await _run_tool("dune_intel", limit=3)
    d = result.data or {}
    if not result.ok:
        await wait.edit_text(f"❌ {_e(d.get('error', 'dune unavailable'))}")
        return
    lines = ["<b>━━━ DUNE ON-CHAIN INTEL ━━━</b>", ""]
    for name, q in (d.get("digest", {}).get("queries") or {}).items():
        if "error" in q:
            continue
        rows = q.get("results") or q.get("data") or []
        lines.append(f"<b>{_e(name)}</b>")
        for row in rows[:3]:
            if isinstance(row, dict):
                kv = " · ".join(f"{_e(k)}: {_e(v)}" for k, v in list(row.items())[:3])
                lines.append(f"  · {kv}")
        lines.append("")
    lines.append("via @rugmunchbot · rugmunch.io")
    await wait.edit_text("\n".join(lines) or "No data", parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def register_integrity(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Wire /volume /exchangeaudit /dune."""
    app.add_handler(CommandHandler("volume", cmd_volume))
    app.add_handler(CommandHandler("exchangeaudit", cmd_exchangeaudit))
    app.add_handler(CommandHandler("dune", cmd_dune))
