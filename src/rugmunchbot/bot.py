#!/usr/bin/env python3
"""
CryptoRugMunch Bot v6 - Module-based architecture (OSS build)
================================================================
Initialization, profile setup, scheduled jobs, and main entry point.
Command handlers and business logic live in handlers/ and supporting modules.

Ported from the live production copy (extraction checklist, Stage 4). All
imports were rewritten from the private monorepo package to ``rugmunchbot.*``
and private constants (owner IDs, channel IDs, branding) replaced with env
configuration per the checklist §3 scrub list.

Run: python3 -m rugmunchbot.bot
"""

from __future__ import annotations

import random
import sys
from datetime import time as dtime  # noqa: F401 — used by daily_scam_tip if re-enabled
from functools import wraps
from typing import Any

import httpx
from telegram import BotCommand, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)
from telegram.request import HTTPXRequest

# NOTE(port): commands/ and handlers/ modules are scaffold stubs in the OSS
# repo (extraction checklist, Stage 4). Every name imported below is preserved
# verbatim from the live bot so the wiring is correct the moment those modules
# are ported — do not rename.
from rugmunchbot import db
from rugmunchbot.admin_state import track_command
from rugmunchbot.alert_worker import deliver_pending_alerts
from rugmunchbot.broadcast_worker import deliver_pending_broadcasts
from rugmunchbot.channels import register_channel_jobs
from rugmunchbot.commands import (
    cmd_chart,
    cmd_events,
    cmd_portfolio,
    cmd_rugmap,
    cmd_simulate,
    cmd_smartmoney,
)
from rugmunchbot.commands.premium import (
    cmd_cluster,
    cmd_deathclock,
    cmd_impersonation,
    cmd_launches,
    cmd_mev,
)
from rugmunchbot.commands.premium_extra import cmd_whale
from rugmunchbot.commands.premium_v2 import (
    cmd_honeypot,
    cmd_laundry,
    cmd_lphealth,
)
from rugmunchbot.config import (
    ALERT_CHECK_INTERVAL,  # TODO(port): not yet in OSS config.py (stripped in the rewrite) — needs env ALERT_CHECK_INTERVAL [REVIEW]
    BOT_DESCRIPTION,  # TODO(port): not yet in OSS config.py — needs env BOT_DESCRIPTION, neutral default (checklist §3) [REVIEW]
    BOT_SHORT_DESCRIPTION,  # TODO(port): not yet in OSS config.py — needs env BOT_SHORT_DESCRIPTION, neutral default (checklist §3) [REVIEW]
    BOT_TOKEN,
    BOT_USERNAME,  # OSS scrub: env-driven handle replaces the hardcoded @RugMunchBot in daily_scam_tip
    CHANNELS,
    SCAM_SCHOOL_TOPICS,
    WEBSITE_URL,
    check_backend_health,  # TODO(port): not yet in OSS config.py — needs the /health ping helper (checklist §3) [REVIEW]
)
from rugmunchbot.handlers.account import (
    cmd_account,
    cmd_buystars,
    cmd_pricing,
    cmd_refer,
    cmd_scamschool,
    cmd_topup,
)
from rugmunchbot.handlers.alerts import check_alerts, cmd_alert, cmd_alerts
from rugmunchbot.handlers.api_access import cmd_api
from rugmunchbot.handlers.ask import cmd_ask
from rugmunchbot.handlers.callback import handle_callback
from rugmunchbot.handlers.chat import cmd_chat
from rugmunchbot.handlers.chat_ai import register_chat_ai
from rugmunchbot.handlers.defi import register_defi
from rugmunchbot.handlers.error import error_handler
from rugmunchbot.handlers.groups import (
    cmd_banuser,
    cmd_groupinfo,
    cmd_mygroup,
    cmd_voicechat,
)
from rugmunchbot.handlers.holders import cmd_holders
from rugmunchbot.handlers.inline import handle_inline
from rugmunchbot.handlers.investigate import cmd_investigate
from rugmunchbot.handlers.market import cmd_news, cmd_rugcheck, register_market
from rugmunchbot.handlers.owner import (
    cmd_admin,
    cmd_admin_ban,
    cmd_admin_broadcast,
    cmd_admin_logs,
    cmd_admin_set_tier,
    cmd_admin_stats,
    cmd_admin_toggle,
    cmd_admin_toggle_chat,
    cmd_admin_unban,
    cmd_admin_user,
)
from rugmunchbot.handlers.payments import precheckout, successful_payment
from rugmunchbot.handlers.prediction import register_prediction
from rugmunchbot.handlers.price import cmd_dex, cmd_price
from rugmunchbot.handlers.report import cmd_report
from rugmunchbot.handlers.research import cmd_research
from rugmunchbot.handlers.scanning import (
    cmd_compare,
    cmd_deepscan,
    cmd_deployer,
    cmd_faq,
    cmd_help,
    cmd_hype,
    cmd_quick,
    cmd_scan,
    cmd_start,
    cmd_ta,
    cmd_wallet,
    sentinel_scan,
)
from rugmunchbot.handlers.sentiment import cmd_sentiment
from rugmunchbot.handlers.trace import cmd_trace, register_trace_commands
from rugmunchbot.handlers.watchlist import (
    cmd_unwatch,
    cmd_watch,
    cmd_watchlist,
)
from rugmunchbot.handlers.webhook import cmd_webhook
from rugmunchbot.states import is_owner
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")

# ══════════════════════════════════════════════
# COMMAND MENU (BotFather) — clean core set, v10
# Telegram's native command list is a flat ordered menu, so "grouping" is
# conveyed by ordering. We register the 11 most-used commands in logical
# order so the composer `/` menu stays fast to scan; the full reference
# (premium/owner/group commands) lives in /help. Admin commands are
# owner-only and intentionally excluded from the public menu.
# Bot API limits: each description ≤ 256 chars, total ≤ 256 chars.
# ══════════════════════════════════════════════
_COMMAND_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Core",
        [
            ("scan", "Scan a token for scams & risks"),
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
]

# Flatten section tuples into the BotFather command list (ordered by section).
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


def install_maintenance_gate(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Globally disable the bot via /admin_toggle (maintenance mode).

    Wraps ``Application.process_update`` so every non-owner update is dropped
    (with a maintenance notice) while the bot is disabled. Reads the Redis
    shared state (admin_state.is_bot_enabled) so the backend admin API and
    the bot agree; fails open (bot enabled) when Redis is down.
    Owner updates always pass through.
    """
    from rugmunchbot import admin_state

    async def guarded(update: object) -> None:
        effective_user = getattr(update, "effective_user", None)
        if effective_user is not None and not is_owner(effective_user.id):
            try:
                enabled = await admin_state.is_bot_enabled()
            except Exception:
                enabled = True
            if not enabled:
                effective_message = getattr(update, "effective_message", None)
                callback_query = getattr(update, "callback_query", None)
                if effective_message is not None:
                    try:
                        site_line = f"\n\U0001f6e1\ufe0f {WEBSITE_URL}" if WEBSITE_URL else ""
                        await effective_message.reply_text(
                            "\U0001f527 <b>Rug Munch is under maintenance</b>\n\n"
                            "We're upgrading the intelligence pipeline \u2014 check back soon!"
                            f"{site_line}",
                            parse_mode="HTML",
                        )
                    except Exception:
                        logger.debug("maintenance notice send failed", exc_info=True)
                elif callback_query is not None:
                    try:
                        await callback_query.answer("Bot is in maintenance mode.", show_alert=True)
                    except Exception:
                        logger.debug("maintenance callback answer failed", exc_info=True)
                return
        await original_process_update(update)

    original_process_update = app.process_update
    app.process_update = guarded  # type: ignore[method-assign]  # intentional monkeypatch


async def _send_ban_notice(update: object) -> None:
    """Send the single banned-user notice on whatever update type arrived."""
    effective_message = getattr(update, "effective_message", None)
    callback_query = getattr(update, "callback_query", None)
    inline_query = getattr(update, "inline_query", None)
    if effective_message is not None:
        try:
            await effective_message.reply_text(
                "\U0001f6ab <b>You are banned from using this bot.</b>",
                parse_mode="HTML",
            )
        except Exception:
            logger.debug("ban notice send failed", exc_info=True)
    elif callback_query is not None:
        try:
            await callback_query.answer("You are banned from using this bot.", show_alert=True)
        except Exception:
            logger.debug("ban callback answer failed", exc_info=True)
    elif inline_query is not None:
        try:
            await inline_query.answer([], cache_time=60, is_personal=True)
        except Exception:
            logger.debug("ban inline answer failed", exc_info=True)


def install_ban_gate(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Block banned users from EVERY command, callback, inline and message.

    Wraps ``Application.process_update`` so the ban check runs before any
    handler is dispatched — commands, callback queries, inline queries,
    pre-checkout and free text all pass through this single gate.

    Consults BOTH authoritative stores:
      * local SQLite ``users.is_banned`` (written by /admin_ban, /admin_unban
        and the admin bot),
      * the Redis set ``bot:admin:banned_users`` (written by the backend admin
        API via ``admin_state.ban_user``).
    Fails open (allows) when a store is unavailable. Owners are always exempt
    so they can still reach /admin_unban. Banned users receive a single
    "You are banned" notice and the update is dropped — no command executes.
    """
    from rugmunchbot import admin_state

    async def guarded(update: object) -> None:
        effective_user = getattr(update, "effective_user", None)
        if effective_user is not None and not is_owner(effective_user.id):
            banned = False
            try:
                banned = await admin_state.is_user_banned(effective_user.id)
            except Exception:
                logger.debug("admin_state:is_user_banned failed", exc_info=True)
            if not banned:
                try:
                    banned = db.is_user_banned(effective_user.id)
                except Exception:
                    logger.debug("db:is_user_banned failed", exc_info=True)
            if banned:
                logger.info("ban-gate blocked user=%s", effective_user.id)
                await _send_ban_notice(update)
                return
        await original_process_update(update)

    original_process_update = app.process_update
    app.process_update = guarded  # type: ignore[method-assign]  # intentional monkeypatch


async def setup_bot_profile(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Set bot description, about text, and command list via BotFather API."""
    bot = app.bot
    try:
        await bot.set_my_description(description=BOT_DESCRIPTION)
        logger.info("Bot description set")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("setup_bot_profile_error", exc_info=True)
        logger.warning(f"Could not set description: {e}")
    try:
        await bot.set_my_short_description(short_description=BOT_SHORT_DESCRIPTION)
        logger.info("Bot short description set")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("setup_bot_profile_error", exc_info=True)
        logger.warning(f"Could not set short description: {e}")
    try:
        commands = _BOT_COMMANDS
        await bot.set_my_commands(commands)
        logger.info(
            "Bot commands registered (%d across %d sections)", len(commands), len(_COMMAND_SECTIONS)
        )
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Could not set commands: {e}")


async def daily_scam_tip(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not CHANNELS.get("main"):
        return
    topic_key = random.choice(list(SCAM_SCHOOL_TOPICS.keys()))  # noqa: S311 — non-crypto topic pick
    topic = SCAM_SCHOOL_TOPICS[topic_key]
    sep_line = "\u2501" * 28
    site_line = f'\U0001f310 <a href="{WEBSITE_URL}">{WEBSITE_URL}</a>' if WEBSITE_URL else ""
    text = (
        f"\U0001f6e1\ufe0f <b>Daily Scam Tip</b>\n{sep_line}\n\n"
        f"{topic['content']}\n\n"
        f"\U0001f4da Learn more: /scamschool\n"
        f"\U0001f50d Scan tokens: @{BOT_USERNAME}\n"
        f"{site_line}"
    )
    try:
        await ctx.bot.send_message(
            CHANNELS["main"], text, parse_mode="HTML", disable_web_page_preview=True
        )
    except TelegramError as e:
        # Channel perms / transient API error — log and let the next daily run retry.
        logger.error(f"Daily tip failed: {e}", exc_info=True)


def main() -> None:
    if not BOT_TOKEN:
        logger.error("RUGMUNCH_BOT_TOKEN not set!")
        sys.exit(1)

    # Force IPv4 for api.telegram.org: this host's IPv6 route is broken and
    # resolves AAAA first, which caused 20+ ConnectTimeouts per 3h and made
    # every outbound call fall through to the global error handler.
    # PTB 21+ drops the `transport` kwarg in favor of `httpx_kwargs`.
    # S104: binding the outbound source to 0.0.0.0 (all IPv4) is intentional —
    # it forces IPv4 and avoids the broken IPv6 route.
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")  # noqa: S104
    request = HTTPXRequest(httpx_kwargs={"transport": transport})

    app = Application.builder().token(BOT_TOKEN).request(request).build()

    # Core commands
    _reg(app, "start", cmd_start)
    _reg(app, "help", cmd_help)
    _reg(app, "faq", cmd_faq)
    _reg(app, "price", cmd_price)
    _reg(app, "dex", cmd_dex)
    _reg(app, "scan", cmd_scan)
    _reg(app, "deepscan", cmd_deepscan)
    _reg(app, "sentinelscan", sentinel_scan)
    _reg(app, "deployer", cmd_deployer)
    _reg(app, "hype", cmd_hype)
    _reg(app, "wallet", cmd_wallet)
    _reg(app, "ta", cmd_ta)
    _reg(app, "compare", cmd_compare)
    _reg(app, "quick", cmd_quick)
    _reg(app, "rugcheck", cmd_rugcheck)

    # Anti-abuse
    _reg(app, "report", cmd_report)

    # Market commands
    register_market(app)  # /market /trending /movers /launches /whales
    register_prediction(app)  # /polymarket /election /odds
    register_defi(app)  # /yield /lpcheck /defi
    _reg(app, "news", cmd_news)
    _reg(app, "sentiment", cmd_sentiment)

    # Research / RAG / freestyle chat
    _reg(app, "research", cmd_research)
    _reg(app, "ask", cmd_ask)
    _reg(app, "chat", cmd_chat)

    # Account / billing
    _reg(app, "account", cmd_account)
    _reg(app, "pricing", cmd_pricing)
    _reg(app, "topup", cmd_topup)
    _reg(app, "buystars", cmd_buystars)
    _reg(app, "scamschool", cmd_scamschool)
    _reg(app, "refer", cmd_refer)

    # Watchlist
    _reg(app, "watchlist", cmd_watchlist)
    _reg(app, "watch", cmd_watch)
    _reg(app, "unwatch", cmd_unwatch)

    # Alerts
    _reg(app, "alerts", cmd_alerts)
    _reg(app, "alert", cmd_alert)

    # External command modules
    _reg(app, "chart", cmd_chart)
    _reg(app, "portfolio", cmd_portfolio)
    _reg(app, "simulate", cmd_simulate)
    _reg(app, "smartmoney", cmd_smartmoney)
    _reg(app, "rugmap", cmd_rugmap)
    _reg(app, "events", cmd_events)

    # Premium intelligence commands (all free during beta)
    _reg(app, "mev", cmd_mev)
    _reg(app, "impersonation", cmd_impersonation)
    _reg(app, "deathclock", cmd_deathclock)
    _reg(app, "launches", cmd_launches)
    _reg(app, "cluster", cmd_cluster)
    _reg(app, "whale", cmd_whale)
    _reg(app, "honeypot", cmd_honeypot)
    _reg(app, "lphealth", cmd_lphealth)
    _reg(app, "laundry", cmd_laundry)

    # Owner / admin panel (owner-only — hidden from BotFather list)
    _reg(app, "admin", cmd_admin)
    _reg(app, "admin_stats", cmd_admin_stats)
    _reg(app, "admin_set_tier", cmd_admin_set_tier)
    _reg(app, "admin_broadcast", cmd_admin_broadcast)
    _reg(app, "admin_toggle", cmd_admin_toggle)
    _reg(app, "admin_toggle_chat", cmd_admin_toggle_chat)
    _reg(app, "admin_user", cmd_admin_user)
    _reg(app, "admin_ban", cmd_admin_ban)
    _reg(app, "admin_unban", cmd_admin_unban)
    _reg(app, "admin_logs", cmd_admin_logs)

    # Premium commands
    _reg(app, "holders", cmd_holders)
    _reg(app, "trace", cmd_trace)
    register_trace_commands(app)  # /dumps /flow
    _reg(app, "investigate", cmd_investigate)
    _reg(app, "webhook", cmd_webhook)
    _reg(app, "api", cmd_api)

    # Group management commands
    _reg(app, "mygroup", cmd_mygroup)
    _reg(app, "groupinfo", cmd_groupinfo)
    _reg(app, "voicechat", cmd_voicechat)
    _reg(app, "banuser", cmd_banuser)

    # Inline, callbacks, messages
    app.add_handler(InlineQueryHandler(handle_inline))
    app.add_handler(CallbackQueryHandler(handle_callback))
    # SINGLE free-text pipeline (merged from the old handle_message + chat_ai):
    # $TOKEN lookup, bare-address auto-scan, known-scam block, owner admin
    # console, then backend RAG chat with news_v2 grounding. There must be NO
    # other MessageHandler(filters.TEXT & ~filters.COMMAND) registered —
    # PTB dispatches to the first matching handler and a second registration
    # would shadow features (the old handle_message shadow bug).
    register_chat_ai(app)

    # Payments
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # Error handler
    app.add_error_handler(error_handler)

    # Global maintenance gate (/admin_toggle off)
    install_maintenance_gate(app)

    # Global ban gate: banned users are blocked at every entry point before
    # any handler runs. Installed AFTER the maintenance gate so the ban check
    # is the outermost wrapper and takes priority (banned users get the ban
    # notice even during maintenance). Owners are exempt in both gates.
    install_ban_gate(app)

    # Scheduled jobs
    job_queue = app.job_queue
    if job_queue:
        # job_queue.run_daily(daily_scam_tip, dtime(hour=14, minute=0, tzinfo=UTC))  # disabled 2026-08-12
        job_queue.run_repeating(check_alerts, interval=ALERT_CHECK_INTERVAL, first=10)
        # Admin-panel broadcasts (queued in Redis by the backend API)
        job_queue.run_repeating(deliver_pending_broadcasts, interval=30, first=15)
        # Intelligent webhook alerts (queued in Redis by the backend webhook
        # engine → telegram_bridge; whale/price/liquidity/launch push alerts)
        job_queue.run_repeating(deliver_pending_alerts, interval=30, first=15)
        # Scheduled channel content: news / alerts / alpha digests (env-driven
        # cadence + per-channel switches; each job fails open).
        register_channel_jobs(app)

    # Startup
    async def startup(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        await check_backend_health()
        await setup_bot_profile(app)

    app.post_init = startup

    logger.info("\U0001f6e1\ufe0f CryptoRugMunch Bot v6 starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
