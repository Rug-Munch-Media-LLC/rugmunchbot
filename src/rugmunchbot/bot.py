#!/usr/bin/env python3
"""
CryptoRugMunch Bot v6 - Module-based architecture
===================================================
Initialization, profile setup, scheduled jobs, and main entry point.
Command handlers and business logic live in handlers/ and supporting modules.

v5: world-class menu (/start categories), freestyle AI chat (/chat), and the
admin control panel (/admin, /admin_stats, /admin_toggle*, /admin_user,
/admin_set_tier, /admin_ban, /admin_unban, /admin_broadcast, /admin_logs).

Run: cd /root/backend && python3 -m app.domains.telegram.rugmunchbot.bot
"""

from __future__ import annotations

import asyncio
import random
import signal
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

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.admin_state import track_command
from app.domains.telegram.rugmunchbot.alert_worker import deliver_pending_alerts
from app.domains.telegram.rugmunchbot.broadcast_worker import deliver_pending_broadcasts
from app.domains.telegram.rugmunchbot.channels import register_channel_jobs
from app.domains.telegram.rugmunchbot.commands import (
    cmd_chart,
    cmd_dune,
    cmd_events,
    cmd_portfolio,
    cmd_rugmap,
    cmd_simulate,
    cmd_smartmoney,
)
from app.domains.telegram.rugmunchbot.commands.premium import (
    cmd_cluster,
    cmd_deathclock,
    cmd_impersonation,
    cmd_mev,
)
from app.domains.telegram.rugmunchbot.commands.premium_extra import cmd_whale
from app.domains.telegram.rugmunchbot.commands.premium_v2 import (
    cmd_honeypot,
    cmd_laundry,
)
from app.domains.telegram.rugmunchbot.config import (
    ALERT_CHECK_INTERVAL,
    BOT_DESCRIPTION,
    BOT_SHORT_DESCRIPTION,
    BOT_TOKEN,
    CHANNELS,
    SCAM_SCHOOL_TOPICS,
    WEBSITE_URL,
    check_backend_health,
)
from app.domains.telegram.rugmunchbot.handlers.account import (
    cmd_account,
    cmd_buystars,
    cmd_pricing,
    cmd_refer,
    cmd_scamschool,
    cmd_topup,
)
from app.domains.telegram.rugmunchbot.handlers.alerts import (
    MONITOR_CHECK_INTERVAL,
    check_alerts,
    check_monitors,
    cmd_alert,
    cmd_alerts,
)
from app.domains.telegram.rugmunchbot.handlers.api_access import cmd_api
from app.domains.telegram.rugmunchbot.handlers.ask import cmd_ask
from app.domains.telegram.rugmunchbot.handlers.callback import handle_callback
from app.domains.telegram.rugmunchbot.handlers.chat import cmd_chat
from app.domains.telegram.rugmunchbot.handlers.chat_ai import register_chat_ai
from app.domains.telegram.rugmunchbot.handlers.deepscan import cmd_deepscan
from app.domains.telegram.rugmunchbot.handlers.defi import cmd_lpcheck, register_defi
from app.domains.telegram.rugmunchbot.handlers.error import error_handler
from app.domains.telegram.rugmunchbot.handlers.groups import (
    cmd_banuser,
    cmd_groupinfo,
    cmd_mygroup,
    cmd_voicechat,
)
from app.domains.telegram.rugmunchbot.handlers.holders import cmd_holders
from app.domains.telegram.rugmunchbot.handlers.inline import handle_inline
from app.domains.telegram.rugmunchbot.handlers.market import cmd_news, cmd_rugcheck, register_market
from app.domains.telegram.rugmunchbot.handlers.osint import (
    cmd_email,
    cmd_face,
    cmd_investigate,
    cmd_osint,
    cmd_osintall,
    cmd_phone,
    cmd_track,
)
from app.domains.telegram.rugmunchbot.handlers.owner import (
    cmd_admin,
    cmd_admin_ban,
    cmd_admin_broadcast,
    cmd_admin_credits,
    cmd_admin_logs,
    cmd_admin_set_tier,
    cmd_admin_stats,
    cmd_admin_toggle,
    cmd_admin_toggle_chat,
    cmd_admin_unban,
    cmd_admin_user,
    cmd_admin_users,
)
from app.domains.telegram.rugmunchbot.handlers.payments import precheckout, successful_payment
from app.domains.telegram.rugmunchbot.handlers.prediction import register_prediction
from app.domains.telegram.rugmunchbot.handlers.price import cmd_dex, cmd_price
from app.domains.telegram.rugmunchbot.handlers.report import cmd_report
from app.domains.telegram.rugmunchbot.handlers.research import cmd_research
from app.domains.telegram.rugmunchbot.handlers.scanning import (
    cmd_compare,
    cmd_deployer,
    cmd_faq,
    cmd_help,
    cmd_hype,
    cmd_menu,
    cmd_quick,
    cmd_scan,
    cmd_start,
    cmd_ta,
    cmd_wallet,
    sentinel_scan,
)
from app.domains.telegram.rugmunchbot.handlers.sentiment import cmd_sentiment
from app.domains.telegram.rugmunchbot.handlers.trace import cmd_trace, register_trace_commands
from app.domains.telegram.rugmunchbot.handlers.watchlist import (
    cmd_monitor,
    cmd_monitors,
    cmd_unmonitor,
    cmd_unwatch,
    cmd_watch,
    cmd_watchlist,
)
from app.domains.telegram.rugmunchbot.handlers.webhook import cmd_webhook
from app.domains.telegram.rugmunchbot.observability import (
    record_error,
    record_job,
    set_scheduler_running,
    start_server,
    stop_server,
)
from app.domains.telegram.rugmunchbot.states import is_owner
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")

# Strong refs to in-flight graceful-shutdown tasks (see install_sigterm_shutdown).
_shutdown_tasks: set[asyncio.Task[Any]] = set()

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
_BOT_COMMANDS: list[BotCommand] = [BotCommand(cmd, desc) for _section, cmds in _COMMAND_SECTIONS for cmd, desc in cmds]


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


def _metrics_job(name: str, fn: Any) -> Any:
    """Wrap a scheduled job with run/failure counters + error logging.

    Additive + cheap: counts every invocation, and on an unexpected
    exception increments the error counter, records the last error and
    logs before re-raising so PTB's error handler still sees it.
    """

    @wraps(fn)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        record_job(name)
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            record_error(kind=f"job:{name}", message=str(e))
            logger.error("scheduled_job_failed name=%s err=%s", name, e, exc_info=True)
            raise

    return wrapped


def install_maintenance_gate(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Globally disable the bot via /admin_toggle (maintenance mode).

    Wraps ``Application.process_update`` so every non-owner update is dropped
    (with a maintenance notice) while the bot is disabled. Reads the Redis
    shared state (admin_state.is_bot_enabled) so the backend admin API and
    the bot agree; fails open (bot enabled) when Redis is down.
    Owner updates always pass through.
    """
    from app.domains.telegram.rugmunchbot import admin_state

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
                        await effective_message.reply_text(
                            "\U0001f527 <b>Rug Munch is under maintenance</b>\n\n"
                            "We're upgrading the intelligence pipeline \u2014 check back soon!\n"
                            "\U0001f6e1\ufe0f rugmunch.io",
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
    from app.domains.telegram.rugmunchbot import admin_state

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
    # Resolve the bot's own identity (get_me) so ``bot.username`` is populated
    # for the free-text group gate (@mention matching in _is_bot_addressed).
    # Fail-soft: if identity cannot be resolved, private DMs and /chat still
    # work and un-@mentioned group chatter stays ignored.
    try:
        await bot.get_me()
        logger.info("Bot identity resolved: @%s", bot.username)
    except Exception as e:
        logger.warning(f"Could not resolve bot identity (mention gate disabled): {e}")
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
        logger.info("Bot commands registered (%d across %d sections)", len(commands), len(_COMMAND_SECTIONS))
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Could not set commands: {e}")


async def daily_scam_tip(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not CHANNELS.get("main"):
        return
    topic_key = random.choice(list(SCAM_SCHOOL_TOPICS.keys()))
    topic = SCAM_SCHOOL_TOPICS[topic_key]
    sep_line = "\u2501" * 28
    text = (
        f"\U0001f6e1\ufe0f <b>Daily Scam Tip</b>\n{sep_line}\n\n"
        f"{topic['content']}\n\n"
        f"\U0001f4da Learn more: /scamschool\n"
        f"\U0001f50d Scan tokens: @RugMunchBot\n"
        f'\U0001f310 <a href="{WEBSITE_URL}">rugmunch.io</a>'
    )
    try:
        await ctx.bot.send_message(CHANNELS["main"], text, parse_mode="HTML", disable_web_page_preview=True)
    except TelegramError as e:
        # Channel perms / transient API error — log and let the next daily run retry.
        logger.error(f"Daily tip failed: {e}", exc_info=True)


# ══════════════════════════════════════════════
# GRACEFUL SHUTDOWN — systemd SIGTERM handling
# ══════════════════════════════════════════════
# PTB's Application.stop() (telegram.ext._application.Application.stop) waits
# *indefinitely* for running jobs and in-flight tasks. The headless Telethon
# MTProto client (app.domains.telegram.headless) is opened by the news / alpha /
# scam-watch sweeps and is never closed by the bot process on its own — a sweep
# awaiting an MTProto call on a wedged socket keeps stop(wait=True) blocked, the
# loop never closes, and systemd kills the cgroup after TimeoutStopSec=10
# (`Failed with result 'timeout'`; `Failed to kill control group ... Invalid
# argument` when the stuck socket read can't even be SIGKILLed).
#
# We therefore replace PTB's default SIGTERM/SIGINT handlers (registered in
# post_init, on the same loop, in the same signal slot) with one that first
# closes the Telethon client (bounded), making in-flight sweeps fail fast, and
# then drives PTB's own graceful stop via stop_running().
# ══════════════════════════════════════════════


async def close_headless_on_shutdown(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Close the singleton Telethon MTProto client (idempotent, bounded).

    Runs before Application.stop() waits on jobs, so in-flight Telethon sweeps
    fail fast instead of blocking shutdown indefinitely.
    """
    from app.domains.telegram.headless import shutdown_headless_client

    try:
        await shutdown_headless_client()
    except Exception:
        logger.warning("headless_client_shutdown_error", exc_info=True)


async def _graceful_shutdown(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Close clients first, then hand control back to PTB's normal stop path."""
    try:
        await close_headless_on_shutdown(app)
    finally:
        app.stop_running()


def install_sigterm_shutdown(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Replace PTB's default stop-signal handlers with shutdown-aware ones.

    Must run on the app's event loop (post_init). add_signal_handler replaces
    the handler PTB registered in run_polling() for the same signal.
    """
    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        logger.info("shutdown_signal_received — closing clients, stopping app")
        # Keep a strong reference so the task is never GC'd mid-shutdown.
        _shutdown_tasks.add(loop.create_task(_graceful_shutdown(app)))

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, RuntimeError):
            logger.debug("signal_handler_not_available sig=%s", sig, exc_info=True)


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
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")  # noqa: S104  # nosec B104 - outbound source bind forces IPv4 to avoid broken IPv6 route (documented above)
    # Bounded connection pool (4) + 20s read/write timeouts so the 4 concurrent
    # update turns don't share a single connection or a tight 5s timeout.
    request = HTTPXRequest(
        connection_pool_size=4,
        read_timeout=20,
        write_timeout=20,
        httpx_kwargs={"transport": transport},
    )

    # Process updates concurrently (N=4): a slow update (e.g. /chat hitting
    # the remote LLM) no longer blocks every other user's command. Bounded
    # at 4 to protect the SQLite-backed db module from concurrency thrash.
    app = Application.builder().token(BOT_TOKEN).request(request).concurrent_updates(4).build()

    # Core commands
    _reg(app, "start", cmd_start)
    _reg(app, "help", cmd_help)
    _reg(app, "menu", cmd_menu)
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
    _reg(app, "dune", cmd_dune)  # curated on-chain intel (Dune queries)

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

    # Monitor (Stars-paid premium)
    _reg(app, "monitor", cmd_monitor)
    _reg(app, "monitors", cmd_monitors)
    _reg(app, "unmonitor", cmd_unmonitor)

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
    _reg(app, "cluster", cmd_cluster)
    _reg(app, "whale", cmd_whale)
    _reg(app, "honeypot", cmd_honeypot)
    _reg(app, "lphealth", cmd_lpcheck)  # alias — /lpcheck is canonical (same backend route)
    _reg(app, "laundry", cmd_laundry)

    # OSINT & Forensics (self-hosted OSINT section)
    _reg(app, "osint", cmd_osint)
    _reg(app, "osintall", cmd_osintall)
    _reg(app, "track", cmd_track)
    _reg(app, "email", cmd_email)
    _reg(app, "phone", cmd_phone)
    _reg(app, "face", cmd_face)
    # /investigate is the OSINT domain router; non-domain args fall through
    # to the Pro-tier deep investigation (see handlers.osint.cmd_investigate).

    # Owner / admin panel (owner-only — hidden from BotFather list)
    _reg(app, "admin", cmd_admin)
    _reg(app, "admin_stats", cmd_admin_stats)
    _reg(app, "credits", cmd_admin_credits)
    _reg(app, "users", cmd_admin_users)
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
        job_queue.run_repeating(_metrics_job("check_alerts", check_alerts), interval=ALERT_CHECK_INTERVAL, first=10)
        # Stars-paid token monitors: re-scan for tax/ownership/mint/LP deltas
        # and push meaningful changes as DMs (live LP/whale/price events flow
        # through the webhook bridge independently).
        job_queue.run_repeating(
            _metrics_job("check_monitors", check_monitors), interval=MONITOR_CHECK_INTERVAL, first=120
        )
        # Admin-panel broadcasts (queued in Redis by the backend API)
        job_queue.run_repeating(
            _metrics_job("deliver_pending_broadcasts", deliver_pending_broadcasts), interval=30, first=15
        )
        # Intelligent webhook alerts (queued in Redis by the backend webhook
        # engine → telegram_bridge; whale/price/liquidity/launch push alerts)
        job_queue.run_repeating(_metrics_job("deliver_pending_alerts", deliver_pending_alerts), interval=30, first=15)
        # Scheduled channel content: news / alerts / alpha digests (env-driven
        # cadence + per-channel switches; each job fails open).
        register_channel_jobs(app)
        set_scheduler_running(True)

    # Startup
    async def startup(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        await check_backend_health()
        await setup_bot_profile(app)
        # Override PTB's default stop-signal handlers (must run on the loop).
        install_sigterm_shutdown(app)

    app.post_init = startup

    # Belt-and-suspenders: if the app stops via ANY path (signal, stop_running()
    # from a handler, fatal error), close the Telethon client before the loop
    # closes. post_stop runs after Application.stop() returns; it is a no-op
    # when the signal path already closed the client. Also stops the
    # observability HTTP server + flips the scheduler gauge.
    async def _post_stop(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        set_scheduler_running(False)
        stop_server()
        await close_headless_on_shutdown(app)

    app.post_stop = _post_stop

    logger.info("\U0001f6e1\ufe0f CryptoRugMunch Bot v6 starting...")
    # Serve /healthz + /metrics for the systemd unit / load balancer (daemon
    # thread; host/port via RUGMUNCHBOT_METRICS_HOST / RUGMUNCHBOT_METRICS_PORT).
    start_server()
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
