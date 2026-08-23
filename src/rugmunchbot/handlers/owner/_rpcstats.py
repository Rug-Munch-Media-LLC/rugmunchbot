#!/usr/bin/env python3
"""
RugMunchBot -- /rpcstats owner command: RPC router health dashboard.

Renders the vendored rpc_router's health report as a compact card:
per-chain alive/dead counts, tier split (free vs keyed freemium),
freemium escalation counter and hourly keyed-provider call volume.
Owner-only — this is operational telemetry, not user-facing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from rugmunchbot.formatters import is_owner, sep, thin_sep
from rugmunchbot.handlers.owner._shared import _deny_admin_attempt
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.rpcstats")


async def cmd_rpcstats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/rpcstats — RPC endpoint pool health (owner only)."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    try:
        from rugmunchbot._vendor.scanner_engines.rpc_router import get_rpc_router

        report = get_rpc_router().get_health_report()
    except Exception as exc:
        logger.debug("rpcstats report failed: %s", exc)
        await update.message.reply_text("❌ RPC router unavailable.")  # type: ignore[union-attr]
        return

    summary = report.get("summary", {})
    lines = ["🩺 <b>RPC Pool Health</b>", sep()]

    lines.append(
        f"Endpoints: <b>{summary.get('total_endpoints', 0)}</b> "
        f"(✅ {summary.get('alive', 0)} / ☠️ {summary.get('dead', 0)})"
    )
    lines.append(
        f"Calls: {summary.get('total_calls', 0)} | Failures: {summary.get('total_failures', 0)}"
    )
    lines.append(
        f"Freemium escalations: <b>{summary.get('freemium_escalations', 0)}</b> | "
        f"keyed calls (1h): {summary.get('freemium_calls_last_hour', 0)}"
    )
    lines.append(thin_sep())

    for chain, endpoints in sorted((report.get("evm_chains") or {}).items()):
        alive = sum(1 for e in endpoints if e.get("alive"))
        keyed_alive = sum(1 for e in endpoints if e.get("alive") and e.get("tier") == "freemium")
        icon = "✅" if alive else "☠️"
        extra = f" +{keyed_alive}🔑" if keyed_alive else ""
        lines.append(f"{icon} <b>{chain}</b>: {alive}/{len(endpoints)}{extra}")

    sol = report.get("solana") or {}
    if sol:
        alive = sum(1 for e in sol.values() if e.get("alive"))
        keyed_alive = sum(1 for e in sol.values() if e.get("alive") and e.get("tier") == "freemium")
        extra = f" +{keyed_alive}🔑" if keyed_alive else ""
        icon = "✅" if alive else "☠️"
        lines.append(f"{icon} <b>solana</b>: {alive}/{len(sol)}{extra}")

    lines.append(sep())
    lines.append("<i>🔑 = keyed freemium providers active</i>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
