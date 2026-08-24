"""/dislocation — cross-venue funding basis monitor.

Unique capability: requires aggregating funding across 7 exchanges. Flags
assets paying wildly different rates on different venues — squeeze fuel,
cash-and-carry setups, or crowding about to unwind.
"""

from __future__ import annotations

import html
from typing import Any

from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

if TYPE_CHECKING:
    from telegram import Update


from rugmunchbot.tools import get_registry


def _e(s: Any) -> str:
    return html.escape(str(s), quote=False)


async def cmd_dislocation(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    wait = await update.message.reply_text("⚡ Scanning 7 venues for funding dislocations...")
    registry = get_registry()
    result = await registry.run("funding_dislocation", limit=8)
    d = result.data or {}
    if not result.ok:
        await wait.edit_text(f"❌ {_e(d.get('error', 'monitor unavailable'))}")
        return

    signals = d.get("signals") or []
    lines = [
        "<b>━━━ FUNDING DISLOCATION MONITOR ━━━</b>",
        f"<i>{d.get('scanned_assets', 0)} assets scanned · "
        f"{d.get('flagged', 0)} flagged across 7 venues</i>",
        "",
    ]
    if not signals:
        lines.append("✅ No notable dislocations — venue funding is aligned.")
    for s in signals:
        emoji = "🚨" if s["severity"] == "EXTREME" else "⚠️"
        rich = s["richest_funding"]
        cheap = s["cheapest_funding"]
        lines.append(
            f"{emoji} <b>{_e(s['asset'])}</b> ({_e(s['severity'])}) — "
            f"OI {_e(s['total_oi_usd'])}"
        )
        lines.append(
            f"   {_e(rich['venue'])} pays {rich['rate'] * 100:+.4f}%/h vs "
            f"{_e(cheap['venue'])} {cheap['rate'] * 100:+.4f}%/h"
        )
        lines.append(f"   💡 {_e(s['read'])}")
        lines.append("")
    lines.append("via @rugmunchbot · rugmunch.io")
    await wait.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def register_dislocation(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    app.add_handler(CommandHandler("dislocation", cmd_dislocation))
