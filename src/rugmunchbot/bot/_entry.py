"""Application entry point — main() orchestrates startup."""

from __future__ import annotations

import sys
from datetime import time as dtime  # noqa: F401 — used by daily_scam_tip if re-enabled
from typing import Any

import httpx
from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    InlineQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)
from telegram.request import HTTPXRequest

from rugmunchbot.bot._commands import (  # noqa: F401
    _BOT_COMMANDS,
    _COMMAND_SECTIONS,
    _reg,
    cmd_channels,
    cmd_feed,
    cmd_site,
)
from rugmunchbot.bot._gates import (
    install_ban_gate,
    install_maintenance_gate,
)
from rugmunchbot.bot._jobs import register_core_jobs
from rugmunchbot.bot._profile import setup_bot_profile
from rugmunchbot.bot._shutdown import (
    close_headless_on_shutdown,
    install_sigterm_shutdown,
)
from rugmunchbot.channels import register_channel_jobs
from rugmunchbot.commands import (
    cmd_chart,
    cmd_dune,
    cmd_events,
    cmd_portfolio,
    cmd_rugmap,
    cmd_simulate,
    cmd_smartmoney,
    cmd_tweet,
    cmd_tweets,
)
from rugmunchbot.commands.premium import (
    cmd_cluster,
    cmd_deathclock,
    cmd_impersonation,
    cmd_mev,
)
from rugmunchbot.commands.premium_extra import cmd_whale
from rugmunchbot.commands.premium_v2 import (
    cmd_honeypot,
    cmd_laundry,
)
from rugmunchbot.config import (
    BOT_TOKEN,
    check_backend_health,
)
from rugmunchbot.handlers.account import (
    cmd_account,
    cmd_buystars,
    cmd_pricing,
    cmd_refer,
    cmd_scamschool,
    cmd_topup,
)
from rugmunchbot.handlers.admin import register_admin_commands
from rugmunchbot.handlers.alerts import (
    cmd_alert,
    cmd_alerts,
)
from rugmunchbot.handlers.api_access import cmd_api
from rugmunchbot.handlers.ask import cmd_ask
from rugmunchbot.handlers.callback import handle_callback
from rugmunchbot.handlers.cascade_token import register_cascade_token
from rugmunchbot.handlers.chat import cmd_chat
from rugmunchbot.handlers.chat_ai import register_chat_ai
from rugmunchbot.handlers.deepscan import cmd_deepscan
from rugmunchbot.handlers.defi import cmd_lpcheck, register_defi
from rugmunchbot.handlers.dislocation import register_dislocation
from rugmunchbot.handlers.error import error_handler
from rugmunchbot.handlers.groups import (
    cmd_banuser,
    cmd_groupinfo,
    cmd_mygroup,
    cmd_voicechat,
)
from rugmunchbot.handlers.hlwhales import register_hlwhales
from rugmunchbot.handlers.whalewatch import register_whalewatch
from rugmunchbot.handlers.holders import cmd_holders
from rugmunchbot.handlers.inline import handle_inline
from rugmunchbot.handlers.integrity_handlers import register_integrity
from rugmunchbot.handlers.liqmap import register_liqmap
from rugmunchbot.handlers._alpha_handlers import cmd_alpha
from rugmunchbot.handlers.market import cmd_news, cmd_rugcheck, register_market
from rugmunchbot.handlers.osint import (
    cmd_email,
    cmd_face,
    cmd_investigate,
    cmd_osint,
    cmd_osintall,
    cmd_phone,
    cmd_track,
)
from rugmunchbot.handlers.owner import (
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
    cmd_rpcstats,
)
from rugmunchbot.handlers.payments import precheckout, successful_payment
from rugmunchbot.handlers.portfolio import register_portfolio
from rugmunchbot.handlers.prediction import register_prediction
from rugmunchbot.handlers.price import cmd_dex, cmd_price
from rugmunchbot.handlers.report import cmd_report
from rugmunchbot.handlers.research import cmd_research
from rugmunchbot.handlers.scanning import (
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
from rugmunchbot.handlers.sentiment import cmd_sentiment
from rugmunchbot.handlers.status import register_status
from rugmunchbot.handlers.trace import cmd_trace, register_trace_commands
from rugmunchbot.handlers.watchlist import (
    cmd_monitor,
    cmd_monitors,
    cmd_unmonitor,
    cmd_unwatch,
    cmd_watch,
    cmd_watchlist,
)
from rugmunchbot.handlers.webhook import cmd_webhook
from rugmunchbot.moderation import register_moderation
from rugmunchbot.observability import (
    set_scheduler_running,
    start_server,
    stop_server,
)
from rugmunchbot.support.logging import get_logger
from rugmunchbot.support.system_alerts import send_system_alert

logger = get_logger("rmi_bot")


def _register_all_handlers(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register every command, callback, message and gate handler on *app*.

    Extracted from ``main()`` (2026-09-20) so the menu-drift guard and the
    tests can build the REAL wiring without starting polling. ``main()``
    calls this once after building the Application.
    """
    # Core commands
    _reg(app, "start", cmd_start)
    _reg(app, "help", cmd_help)
    _reg(app, "channels", cmd_channels)
    _reg(app, "feed", cmd_feed)
    _reg(app, "site", cmd_site)
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
    _reg(app, "alpha", cmd_alpha)  # premium alpha signals feed

    # Anti-abuse
    _reg(app, "report", cmd_report)

    # Market commands
    register_market(app)  # /market /trending /movers /launches /whales
    register_integrity(app)  # /volume /exchangeaudit /dune
    register_liqmap(app)  # /liqmap
    register_hlwhales(app)  # /hlwhales
    register_whalewatch(app)  # /whalewatch — premium whale-wallet deep-dive
    register_dislocation(app)  # /dislocation
    register_status(app)  # /status
    register_portfolio(app)  # /portfolio
    register_cascade_token(app)  # /cascadewatch /tokeninfo
    register_prediction(app)  # /polymarket /election /odds
    register_defi(app)  # /yield /lpcheck /defi
    _reg(app, "news", cmd_news)
    _reg(app, "sentiment", cmd_sentiment)

    # Research / RAG / freestyle chat
    _reg(app, "research", cmd_research)
    _reg(app, "ask", cmd_ask)
    _reg(app, "chat", cmd_chat)
    _reg(app, "dune", cmd_dune)  # curated on-chain intel (Dune queries)

    # X post reader (ApiPass — never direct scrape)
    _reg(app, "tweet", cmd_tweet)
    _reg(app, "tweets", cmd_tweets)

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

    # @rmi_admin_bot server-management commands (owner-only, hidden from menu)
    register_admin_commands(app)

    # Premium commands
    _reg(app, "holders", cmd_holders)
    _reg(app, "trace", cmd_trace)
    register_trace_commands(app)  # /dumps /flow
    _reg(app, "investigate", cmd_investigate)
    _reg(app, "webhook", cmd_webhook)
    _reg(app, "api", cmd_api)
    _reg(app, "rpcstats", cmd_rpcstats)  # owner: RPC pool health

    # Group management commands
    _reg(app, "mygroup", cmd_mygroup)
    _reg(app, "groupinfo", cmd_groupinfo)
    _reg(app, "voicechat", cmd_voicechat)
    _reg(app, "banuser", cmd_banuser)

    # Channel guard / moderation — handler group -1, so a deleted spam
    # message never reaches the AI chat pipeline. Inert for every chat
    # missing from configs/moderation.json; dry-run by default.
    register_moderation(app)

    # Inline, callbacks, messages
    app.add_handler(InlineQueryHandler(handle_inline))
    app.add_handler(CallbackQueryHandler(handle_callback))
    # Main-channel approval-gate Edit flow (2026-09-04): a NARROW handler that
    # only fires for replies to @rmi_admin_bot approval messages (the replied-to
    # message carries the approval marker). Registered BEFORE the general
    # free-text pipeline so an approval-edit reply is consumed here and never
    # reaches the AI chat. It does not shadow general free text because it
    # matches only replies to our approval messages.

    from rugmunchbot.channels.scheduler._posts_rag import _handle_approval_edit_reply

    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT, _handle_approval_edit_reply))

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




    _register_all_handlers(app)

    # Scheduled jobs
    job_queue = app.job_queue
    if job_queue:
        # job_queue.run_daily(daily_scam_tip, dtime(hour=14, minute=0, tzinfo=UTC))  # disabled 2026-08-12
        register_core_jobs(app)
        # Scheduled channel content: news / alerts / alpha digests (env-driven
        # cadence + per-channel switches; each job fails open).
        register_channel_jobs(app)
        set_scheduler_running(True)

    # Startup

    async def assert_command_menu(
        app: Application[Any, Any, Any, Any, Any, Any],
    ) -> None:
        """Win 72: re-assert the command menu on every startup.

        Restarts and concurrent setMyCommands calls had silently regressed
        the menu from 18 to 12 commands. This makes the desired state
        declarative — whatever is in the bot process IS the menu.
        """
        # BotCommand OBJECTS, not dicts — PTB's set_my_commands does not
        # convert dicts and fails with KeyError('0') (logged as err=0),
        # which silently kept the menu stale on every boot until now.
        commands = [BotCommand(command=c.command, description=c.description) for c in _BOT_COMMANDS]
        try:
            await app.bot.set_my_commands(commands)
            logger.info("command_menu_asserted count=%d", len(commands))
        except Exception as exc:
            logger.warning("command_menu_assert_failed err=%s", str(exc)[:120])

    async def startup(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        await check_backend_health()
        await setup_bot_profile(app)
        await assert_command_menu(app)
        await send_system_alert(app.bot, "low", "bot", "system online — bot started")
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
