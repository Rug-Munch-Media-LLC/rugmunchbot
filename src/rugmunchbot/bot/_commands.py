"""Bot command registration — /help sections and BotFather sync."""

from __future__ import annotations

from datetime import time as dtime  # noqa: F401 — used by daily_scam_tip if re-enabled
from functools import wraps
from typing import Any

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from rugmunchbot import db
from rugmunchbot.admin_state import track_command
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")


_COMMAND_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Core",
        [
            ("scan", "Scan a token for scams & risks"),
            ("deepscan", "Deep token scan — full forensic audit"),
            ("wallet", "Wallet forensics & analysis"),
            ("price", "Token price by address"),
            ("chart", "Chart & technical analysis"),
            ("trace", "Cross-chain fund trace"),
            ("market", "Market overview & trends"),
            ("news", "Latest crypto news"),
            ("alerts", "Manage price alerts"),
            ("chat", "Freestyle AI chat"),
            ("help", "Full command guide"),
            ("start", "Welcome & quick start"),
        ],
    ),
    (
        "Security Forensics",
        [
            ("volume", "Wash-trading / fake-volume check on a token"),
            ("exchangeaudit", "Rank exchanges by wash-trading risk"),
            ("liqmap", "Cross-exchange liquidation pressure map (7 venues)"),
            ("hlwhales", "Hyperliquid whale flow + squeeze watch"),
            ("dislocation", "Cross-venue funding dislocation monitor"),
            ("dune", "On-chain analytics from Dune"),
            ("status", "Live data-source tier health"),
        ],
    ),
    (
        # 2026-08-25 capability-sync: these were registered but menu-invisible
        # (B10 audit) — users could not discover them via the BotFather menu.
        "Markets & Discovery",
        [
            ("trending", "Hot tokens right now"),
            ("movers", "Biggest 24h gainers & losers"),
            ("launches", "New token launch feed"),
            ("whales", "Recent whale moves feed"),
            ("yield", "DeFi yield opportunities"),
            ("defi", "DeFi protocol analytics"),
            ("polymarket", "Prediction market odds"),
            ("election", "Election odds markets"),
            ("odds", "Sports & event odds"),
            ("tokeninfo", "Token DNA fingerprint"),
            ("cascadewatch", "Liquidation cascade risk for a token"),
            ("portfolio", "Wallet holdings overview"),
            ("lpcheck", "LP health & lock check"),
        ],
    ),
    (
        # 2026-08-25 capability audit: 67 registered commands were
        # menu-invisible. These are the high-value user-facing ones (admin/
        # owner-gated and auxiliary aliases stay unadvertised deliberately).
        "Power Tools",
        [
            ("rugcheck", "Quick rug-pull risk check"),
            ("honeypot", "Honeypot simulation — can you sell?"),
            ("deployer", "Deployer fingerprint & history"),
            ("holders", "Holder distribution analysis"),
            ("smartmoney", "Smart-money wallet tracking"),
            ("sentinelscan", "SENTINEL deep forensic scan"),
            ("investigate", "Full token investigation dossier"),
            ("whale", "Single whale wallet deep-dive"),
            ("mev", "MEV & sandwich exposure check"),
            ("compare", "Compare two tokens side-by-side"),
            ("simulate", "Simulate a buy/sell before you trade"),
            ("research", "Deep research report on a token"),
            ("sentiment", "Social sentiment breakdown"),
            ("dex", "DEX pair & liquidity lookup"),
        ],
    ),
]


_BOT_COMMANDS: list[BotCommand] = [
    BotCommand(cmd, desc) for _section, cmds in _COMMAND_SECTIONS for cmd, desc in cmds
]


def _reg(app: Application[Any, Any, Any, Any, Any, Any], name: str, handler) -> None:
    """Register a command with per-command activity tracking (for /admin_stats).

    Tracks into the Redis-backed fleet analytics (admin_state.track_command,
    shared with the backend admin API) AND the local SQLite daily stats.
    """

    @wraps(handler)
    async def tracked(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        db.record_activity(cmd=True)
        try:
            await track_command(name)
        except Exception:
            logger.debug("admin_state track_command failed", exc_info=True)
        await handler(update, ctx)

    app.add_handler(CommandHandler(name, tracked))
