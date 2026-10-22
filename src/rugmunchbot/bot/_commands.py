"""Bot command registration — advertised menu, /more catalog, BotFather sync.

Progressive disclosure (2026-10-23): the BotFather menu advertises the **20**
highest-value commands, not all 95 public commands. A 43-command wall in the
Telegram menu is a product failure — nobody reads it, and it buries /scan.
Everything else stays registered and discoverable through ``/more`` (the full
catalog in ``bot/_catalog.py``) and ``/help``.

Three rules this module enforces:

* ``_COMMAND_SECTIONS`` — the advertised 20. The BotFather menu is pushed
  from exactly this list (``_BOT_COMMANDS``), so menu and code cannot drift.
* ``_MENU_COMMANDS`` — the advertised set, used by the startup guard.
* Everything registered but not advertised is *intentional* progressive
  disclosure, not drift — ``bot/_menu_guard.py`` checks the direction that
  matters (advertised ⇒ registered, ≤ 20, no duplicates).

The ``/desk``, ``/plans`` and ``/more`` handlers are registered by
:func:`register_surface_commands` (called from ``setup_bot_profile``) so the
menu owner is also the surface owner.
"""

from __future__ import annotations

import os
import re
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

#: Hard ceiling for the advertised BotFather menu. Progressive disclosure:
#: 20 is the most a human scans in one screen; the rest live under /more.
MAX_ADVERTISED = 20

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


#: The advertised surface: 20 commands, five intent groups. Anything not
#: listed here is still registered and reachable — it is simply not shouting
#: at a first-time user from the menu. Add to this list only by removing
#: something else (the guard enforces the ceiling).
_COMMAND_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Start here",
        [
            ("start", "Welcome & 5-command quickstart"),
            ("scan", "Scan a token for scams & risks"),
            ("desk", "Latest write-ups from the desk"),
            ("plans", "Free vs Pro — stated honestly"),
            ("help", "How the bot works"),
        ],
    ),
    (
        "Scan & safety",
        [
            ("deepscan", "Deep scan — full forensic audit"),
            ("wallet", "Wallet forensics & analysis"),
            ("holders", "Holder distribution analysis"),
            ("trace", "Cross-chain fund trace"),
        ],
    ),
    (
        "Markets",
        [
            ("price", "Token price by address"),
            ("chart", "Chart & technical analysis"),
            ("trending", "Hot tokens right now"),
            ("market", "Market overview & trends"),
        ],
    ),
    (
        "Intel",
        [
            ("news", "Latest crypto news"),
            ("research", "Deep research report on a token"),
            ("alerts", "Manage price alerts"),
            ("portfolio", "Your portfolio: value, P&L, 24h"),
        ],
    ),
    (
        "Account",
        [
            ("account", "Your account, tier & usage"),
            ("report", "Report a scam / rug"),
            ("more", "All commands, grouped by category"),
        ],
    ),
]


_BOT_COMMANDS: list[BotCommand] = [
    BotCommand(cmd, desc) for _section, cmds in _COMMAND_SECTIONS for cmd, desc in cmds
]

#: Advertised command names (menu = source of truth for the user surface).
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
    """Startup drift check: advertised menu vs registry (log loudly, never crash).

    Only the direction that hurts users is an error: an advertised command
    with no handler (a dead menu tap) or a menu that exceeds
    ``MAX_ADVERTISED``. Registered-but-unadvertised commands are the design
    (they live under /more), so they are logged as info, not drift.
    """
    registered = collect_registered_commands(app)
    phantom = sorted(_MENU_COMMANDS - registered)
    duplicates = sorted(_duplicates())
    if phantom:
        logger.error(
            "MENU_DRIFT: %d menu commands are NOT registered (broken menu): %s",
            len(phantom),
            ", ".join(phantom),
        )
    if duplicates:
        logger.error("MENU_DRIFT: duplicate menu entries: %s", ", ".join(duplicates))
    if len(_MENU_COMMANDS) > MAX_ADVERTISED:
        logger.error(
            "MENU_DRIFT: %d advertised commands exceeds the %d ceiling",
            len(_MENU_COMMANDS),
            MAX_ADVERTISED,
        )
    if not phantom and not duplicates and len(_MENU_COMMANDS) <= MAX_ADVERTISED:
        logger.info(
            "Menu coverage OK: %d registered, %d advertised, %d behind /more",
            len(registered),
            len(_MENU_COMMANDS),
            len(registered - _MENU_COMMANDS),
        )
    _check_help_text_coverage(registered)


def _duplicates() -> set[str]:
    """Menu entry names that appear more than once (a duplicated menu row)."""
    seen: set[str] = set()
    dupes: set[str] = set()
    for bc in _BOT_COMMANDS:
        (dupes if bc.command in seen else seen).add(bc.command)
    return dupes


def _check_help_text_coverage(registered: set[str]) -> None:
    """Every ``/cmd`` mentioned in /help or /more text must be registered.

    Both texts are GENERATED from the registry (``bot/_catalog.py`` +
    ``_COMMAND_SECTIONS``), so this is a belt-and-suspenders check that a
    hand-written line never sneaks back in.
    """
    from rugmunchbot.handlers._help_text import HELP_TEXT, MENU_TEXT

    # Negative lookbehind skips URL paths (t.me/x, rugmunch.io/pricing) —
    # only bare command mentions count.
    mentioned = set(re.findall(r"(?<![\w./@<])/([a-z_]{2,30})\b", HELP_TEXT + MENU_TEXT))
    unknown = sorted(mentioned - registered)
    if unknown:
        logger.error(
            "HELP_DRIFT: /help or /more reference unregistered commands: %s",
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


def register_surface_commands(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register the product-surface commands that are not in ``_entry``.

    ``/desk`` (content), ``/plans`` (pricing truth) and ``/more`` (catalog)
    are owned by the menu surface, so ``setup_bot_profile`` registers them
    right before the drift guard runs. Idempotent: a command already
    registered by an older wiring is left untouched (no duplicate handlers).
    """
    from rugmunchbot.handlers._desk import cmd_desk
    from rugmunchbot.handlers._more import cmd_more
    from rugmunchbot.handlers._plans import cmd_plans

    existing = collect_registered_commands(app)
    for name, handler in (("desk", cmd_desk), ("plans", cmd_plans), ("more", cmd_more)):
        if name not in existing:
            _reg(app, name, handler)


# ── Channel-shill commands (menu "Our Channels") ────────────────────────

_CHANNELS_TEXT = (
    "🥊 <b>The RugMunch Network</b>\n\n"
    "📢 @cryptorugmuncher — main: The Morning Rug, "
    "Midday Market Update, top signals (mirrored to feed.rugmunch.io)\n"
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
            "The Morning Rug + Midday Market Update publish there first."
        )


async def cmd_site(update: Any, context: Any) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "🛡️ rugmunch.io — the full RugMunch scanner:\n"
            "50+ security modules, AI analyst verdicts, wallet forensics,\n"
            "cross-chain tracing. Free to use."
        )
