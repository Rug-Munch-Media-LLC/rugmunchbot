"""/cascadewatch + /tokeninfo — cascade risk + DNA fingerprint commands."""

from __future__ import annotations

import html
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from rugmunchbot.tools import get_registry


def _e(s: Any) -> str:
    return html.escape(str(s), quote=False)


async def cmd_cascadewatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    wait = await update.message.reply_text("⚡ Scanning liquidation cascades...")
    registry = get_registry()
    result = await registry.run("cascade_watch", limit=8)
    d = result.data if hasattr(result, "data") else {}
    if not getattr(result, "ok", False):
        err = d.get("error", "unavailable") if isinstance(d, dict) else "unavailable"
        await wait.edit_text(f"❌ {_e(err)}")
        return

    headline = d.get("headline", "")
    venues = ", ".join(d.get("venues_online") or [])
    total_oi = d.get("total_oi_usd", 0)
    lines = [
        "⚡ <b>CASCADE WATCH</b>",
        f"<i>{_e(headline)} · {_e(venues)} · OI {total_oi / 1e9:.1f}B</i>",
        "",
    ]
    for row in (d.get("watchlist") or [])[:8]:
        band = row.get("band", "?")
        emoji = {"IMMINENT": "🔴", "DANGER": "🟠", "WATCH": "🟡", "CLEAR": "🟢"}.get(band, "⚪")
        lines.append(
            f"{emoji} <b>{_e(row['asset'])}</b> — {band} | "
            f"OI {row.get('total_oi_usd', 0) / 1e9:.1f}B | "
            f"liq 10x @ {row.get('long_liq_10x', '?')}"
        )
    lines.extend(["", "via @rugmunchbot · rugmunch.io"])
    await wait.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_tokeninfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not ctx.args:
        await update.message.reply_text(
            "🧬 <b>Token DNA Fingerprint</b>\n\n"
            "Usage: <code>/tokeninfo &lt;address&gt;</code>\n"
            "Behavioral similarity matching vs known rug patterns.",
            parse_mode=ParseMode.HTML,
        )
        return
    address = ctx.args[0].strip()
    wait = await update.message.reply_text("🧬 Sequencing token DNA...")
    registry = get_registry()
    result = await registry.run("token_dna", scan_result={"address": address})
    d = result.data if hasattr(result, "data") else {}
    if not getattr(result, "ok", False):
        err = d.get("error", "analysis failed — need scan data first")
        await wait.edit_text(f"❌ {_e(err)}")
        return

    cls = d.get("classification", {})
    best = cls.get("best_match", {})
    dna = d.get("dna", {})
    threat = cls.get("threat_level", "?")
    emoji = {"CRITICAL_MATCH": "🚨", "HIGH_SIMILARITY": "⚠️",
             "MODERATE_RESEMBLANCE": "📊", "LOW_RESEMBLANCE": "✅"}.get(threat, "⚪")

    lines = [
        f"{emoji} <b>TOKEN DNA FINGERPRINT</b>",
        f"<i>{_e(cls.get('read', ''))}</i>",
        "",
        "<b>Behavioral Dimensions:</b>",
    ]
    for dim, val in sorted(dna.items(), key=lambda x: x[1], reverse=True):
        bar = "█" * int(val * 10) + "░" * (10 - int(val * 10))
        lines.append(f"  {bar} {_e(dim.replace('_', ' '))}: {val:.2f}")
    lines.extend(["", "via @rugmunchbot · rugmunch.io"])
    await wait.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def register_cascade_token(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    app.add_handler(CommandHandler("cascadewatch", cmd_cascadewatch))
    app.add_handler(CommandHandler("tokeninfo", cmd_tokeninfo))
