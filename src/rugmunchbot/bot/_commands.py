"""Bot command registration — /help sections and BotFather sync."""

from __future__ import annotations

import os
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
from rugmunchbot.flood_control import check_flood as _check_flood
from rugmunchbot.states import is_owner
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")

#: Per-command flood gate (2026-09-01). The free-text AI surface was already
#: flood-limited (3/s, 30/min); command handlers were not, so a user could
#: spam /scan 100x/second. This gate reuses the SAME FloodController (same
#: Redis/SQLite sliding-window counters, same 3/s + 30/min limits, owners
#: exempt) so both surfaces share one per-user budget. Env-gated so it can be
#: disabled in an emergency. Rejections reply and return BEFORE recording
#: activity so a flooded command never inflates /admin_stats.
_COMMAND_FLOOD_ENABLED = os.getenv("RMI_COMMAND_FLOOD_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)


_COMMAND_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Our Channels",
        [
            ("channels", "All RugMunch channels & what they serve"),
            ("feed", "feed.rugmunch.io — the written digest"),
            ("site", "rugmunch.io — full scanner platform"),
        ],
    ),
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
            ("tweet", "Read an X post by URL, id, or @handle (via ApiPass)"),
            ("tweets", "Recent X posts from an account"),
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

#: Advertised command names (menu = source of truth for user-facing surface).
_MENU_COMMANDS: frozenset[str] = frozenset(bc.command for bc in _BOT_COMMANDS)


def collect_registered_commands(app: Application[Any, Any, Any, Any, Any, Any]) -> set[str]:
    """Collect every CommandHandler name registered on *app*.

    Covers all registration paths (``_reg`` and the ``register_*`` helpers
    that call ``app.add_handler`` directly).
    """
    names: set[str] = set()
    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, CommandHandler):
                names.update(handler.commands)
    return names


def check_menu_coverage(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Startup drift check: registry vs menu (log loudly, never crash).

    The menu (``_COMMAND_SECTIONS``) is the source of truth for what users
    see. Anything registered but unadvertised is drift; anything advertised
    but not registered is a broken menu entry. Also verifies every ``/cmd``
    mentioned in the /help and /menu texts is actually registered.
    """
    registered = collect_registered_commands(app)
    unadvertised = sorted(registered - _MENU_COMMANDS)
    if unadvertised:
        logger.error(
            "MENU_DRIFT: %d registered commands are not in the menu: %s",
            len(unadvertised),
            ", ".join(unadvertised),
        )
    phantom = sorted(_MENU_COMMANDS - registered)
    if phantom:
        logger.error(
            "MENU_DRIFT: %d menu commands are NOT registered (broken menu): %s",
            len(phantom),
            ", ".join(phantom),
        )
    if not unadvertised and not phantom:
        logger.info(
            "Menu coverage OK: %d registered, %d advertised",
            len(registered),
            len(_MENU_COMMANDS),
        )
    _check_help_text_coverage(registered)


def _check_help_text_coverage(registered: set[str]) -> None:
    """Every ``/cmd`` mentioned in /help or /menu text must be registered."""
    import re

    from rugmunchbot.handlers._help_text import HELP_TEXT, MENU_TEXT

    # Negative lookbehind skips URL paths (t.me/x, rugmunch.io/pricing) —
    # only bare command mentions count.
    mentioned = set(
        re.findall(r"(?<![\w./@<])/([a-z_]{2,30})\b", HELP_TEXT + MENU_TEXT)
    )
    unknown = sorted(mentioned - registered)
    if unknown:
        logger.error(
            "HELP_DRIFT: /help or /menu reference unregistered commands: %s",
            ", ".join(unknown),
        )


def _reg(app: Application[Any, Any, Any, Any, Any, Any], name: str, handler) -> None:
    """Register a command with per-command activity tracking (for /admin_stats).

    Tracks into the Redis-backed fleet analytics (admin_state.track_command,
    shared with the backend admin API) AND the local SQLite daily stats. Also
    enforces the per-user flood gate (3/s, 30/min) so repeated command spam
    is throttled before it reaches a handler.
    """

    @wraps(handler)
    async def tracked(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = getattr(update, "effective_user", None)
        # Per-user command flood gate (same controller/counters as the
        # free-text AI surface). Owners exempt. A rejection replies and
        # returns BEFORE activity tracking so it never inflates /admin_stats.
        if _COMMAND_FLOOD_ENABLED and user is not None and not is_owner(user.id):
            try:
                allowed, retry_after, _receipt = await _check_flood(user.id)
            except Exception:
                allowed, retry_after = True, 0
            if not allowed:
                logger.debug("command flood blocked cmd=%s user=%s", name, user.id)
                if update.effective_message:
                    try:
                        await update.effective_message.reply_text(
                            f"\U0001f6a6 <b>Slow down!</b> Please wait <b>{retry_after}s</b> "
                            "before issuing more commands.",
                            parse_mode="HTML",
                        )
                    except Exception:
                        logger.debug("command flood reply failed", exc_info=True)
                return
        db.record_activity(cmd=True)
        try:
            await track_command(name)
        except Exception:
            logger.debug("admin_state track_command failed", exc_info=True)
        await handler(update, ctx)

    app.add_handler(CommandHandler(name, tracked))


# ── Channel-shill commands (menu "Our Channels") ────────────────────────

_CHANNELS_TEXT = (
    "🥊 <b>The RugMunch Network</b>\n\n"
    "📢 @cryptorugmuncher — main: The Morning Rug Market Report, "
    "The Midday Rug Market Report, top signals (mirrored to feed.rugmunch.io)\n"
    "🚨 @rmialerts — imminent rugs, hacks & exploits ONLY. If it posts, act.\n"
    "🐋 whales — whale moves & large transfers\n"
    "🧠 alpha — alpha-category stories under the main gate\n"
    "🔍 @rmiscans — scanner cards for every flagged token\n"
    "📰 news — the full firehose, reranked\n"
    "📝 changelog — what we shipped on rugmunch.io each day\n\n"
    "🌐 feed.rugmunch.io — every main-channel story, written long-form\n"
    "🛡️ rugmunch.io — the full scanner: 50+ modules, AI verdicts"
)


async def cmd_channels(update: Any, context: Any) -> None:
    """List every RugMunch channel and what it serves."""
    if update.effective_message:
        await update.effective_message.reply_text(_CHANNELS_TEXT)


async def cmd_feed(update: Any, context: Any) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "📖 feed.rugmunch.io — every main-channel story, written long-form.\n"
            "The Morning Rug Market Report + The Midday Rug Market Report publish there first."
        )


async def cmd_site(update: Any, context: Any) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "🛡️ rugmunch.io — the full RugMunch scanner:\n"
            "50+ security modules, AI analyst verdicts, wallet forensics,\n"
            "cross-chain tracing. Free to use."
        )
