#!/usr/bin/env python3
"""Premium command surface: /premium, /cancelscan, /scamfeed, /koltrack.

Every command obeys the same shape: gate first (so an unpaid user gets the
upgrade path, not a stack trace), then the answer in one message. None of them
touch the network here — the data they render arrives from the caller, which
keeps these handlers testable without a Telegram client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from rugmunchbot.premium import gating, kol_tracker, quota, scam_feed
from rugmunchbot.premium.deepscan_job import REGISTRY, render_progress
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot.premium")


async def _say(update: Update, text: str) -> None:
    """One reply, HTML, no reply-to chain (the bot answers, it does not argue)."""
    message = update.effective_message
    if message is not None:
        await message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_premium(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/premium — quota meter, reset clock, upgrade path."""
    user = update.effective_user
    if user is None:
        return
    usage = quota.read(user.id)
    await _say(update, quota.render(usage))


async def cmd_cancelscan(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/cancelscan — stop the user's live forensic scan."""
    user = update.effective_user
    if user is None:
        return
    job = REGISTRY.active(user.id)
    if job is None:
        await _say(update, "No scan is running. Start one with /deepscan &lt;address&gt;.")
        return
    REGISTRY.cancel(job.job_id)
    await _say(update, render_progress(REGISTRY.get(job.job_id) or job))


async def cmd_scamfeed(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/scamfeed — subscriber-only scam callouts with evidence."""
    user = update.effective_user
    if user is None:
        return
    allowed, why = scam_feed.subscriber_only(user.id)
    if not allowed:
        await _say(update, why)
        return
    callouts = _callouts(ctx)
    await _say(update, scam_feed.feed_preview(callouts))


async def cmd_koltrack(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/koltrack — declared KOL wallets, or a declaration request."""
    user = update.effective_user
    if user is None:
        return
    allowed, why = gating.gate(user.id, "kol_tracker")
    if not allowed:
        await _say(update, why)
        return
    args = [a for a in (ctx.args or []) if a]
    registry = _registry(ctx)
    if not args:
        await _say(update, kol_tracker.render_listing(registry))
        return
    if args[0].lower() != "add" or len(args) < 4:
        await _say(
            update,
            "Usage: <code>/koltrack add &lt;handle&gt; &lt;address&gt; &lt;chain&gt;</code>\n"
            "Declared wallets only — inferred wallets are never tracked.",
        )
        return
    handle, address, chain = args[1], args[2], args[3]
    ok = registry.declare(
        kol_tracker.Declaration(handle=handle, address=address, chain=chain, source="self-declared")
    )
    await _say(
        update,
        f"✅ Tracking <b>@{handle.lstrip('@')}</b> on {chain}."
        if ok
        else "❌ That declaration was not accepted — address and handle are both required.",
    )


def _registry(ctx: ContextTypes.DEFAULT_TYPE) -> kol_tracker.Registry:
    """The process-wide KOL registry, kept on the application for durability."""
    app: Any = ctx.application
    registry = getattr(app, "kol_registry", None)
    if registry is None:
        registry = kol_tracker.Registry()
        app.kol_registry = registry
    return registry


def _callouts(ctx: ContextTypes.DEFAULT_TYPE) -> list[scam_feed.Callout]:
    """Callouts staged by the scanner lane; empty when the lane has not run."""
    app: Any = ctx.application
    return list(getattr(app, "scam_callouts", ()) or ())


def register_premium(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Attach the premium commands. Idempotent per process by construction."""
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("cancelscan", cmd_cancelscan))
    app.add_handler(CommandHandler("scamfeed", cmd_scamfeed))
    app.add_handler(CommandHandler("koltrack", cmd_koltrack))
    logger.info("premium:registered", commands=4)
