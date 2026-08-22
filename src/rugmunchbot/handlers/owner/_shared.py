"""Owner/admin shared helpers — audit log, deny gate, usage + credit blocks.

Extracted verbatim from the former single-module ``handlers/owner.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rugmunchbot import db
from rugmunchbot._vendor import access_guard
from rugmunchbot.config import OWNER_IDS
from rugmunchbot.formatters import thin_sep
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update

logger = get_logger("rmi_bot")

# Pending broadcast confirmation state (keyed by owner user_id).
_pending_broadcast: dict[int, str] = {}

# Alert target for denied attempts: the owner's private chat (chat id ==
# user id for private chats). Pick the first owner deterministically.
_OWNER_ALERT_CHAT: int = next(iter(sorted(OWNER_IDS)), 0)


def _log(admin_id: int, action: str, details: str = "") -> None:
    db.log_admin_action(admin_id, action, details)


async def _deny_admin_attempt(update: Update, ctx) -> None:
    """Log + alert a non-owner admin attempt (defense-in-depth). Fail-open."""
    user = update.effective_user
    chat = update.effective_chat
    surface = "admin-cmd"
    if update.callback_query is not None and update.callback_query.data:
        surface = f"cb:{update.callback_query.data}"
    elif update.message is not None and update.message.text:
        surface = f"cmd:{update.message.text.split()[0]}"
    await access_guard.deny_admin_attempt(
        bot=ctx.bot,
        owner_chat_id=_OWNER_ALERT_CHAT,
        bot_name="rugmunchbot",
        surface=surface,
        user_id=user.id if user else None,
        username=user.username if user else None,
        chat_id=chat.id if chat else None,
        chat_type=chat.type if chat else None,
        audit=db.log_admin_action,
    )


def _usage_line(uid: int) -> str:
    scans, ai = db.get_weekly_usage(uid)
    return f"Scans: {scans} | AI msgs: {ai}"


def _format_model_usage_line() -> str:
    """Compact per-model chat usage block for the /admin dashboard.

    Reads the lightweight in-process counter (tier / model / latency /
    tokens) recorded by every successful chat LLM call. Process-lifetime
    totals; the durable per-answer audit trail stays in the JSON trace logs.
    """
    try:
        from rugmunchbot.model_usage import snapshot as model_usage_snapshot

        snap = model_usage_snapshot()
    except Exception as exc:
        logger.debug("model usage snapshot unavailable: %s", exc)
        return "\U0001f916 Model usage: <i>unavailable</i>"
    if not snap.get("total_calls"):
        return "\U0001f916 Model usage: <i>no chat calls since start</i>"
    tiers = snap.get("tiers", {})
    tier_line = (
        " | ".join(
            f"{t}: {tiers[t]['calls']} calls, {tiers[t]['tokens']:,} tok"
            for t in ("snappy", "long", "fallback", "backend")
            if t in tiers
        )
        or "n/a"
    )
    top_models = (
        " | ".join(
            f"{m}: {md['calls']} calls" for m, md in list(snap.get("models", {}).items())[:4]
        )
        or "n/a"
    )
    avg_latency = (
        round(snap["total_latency_ms"] / snap["total_calls"], 0) if snap.get("total_calls") else 0
    )
    return (
        f"\U0001f916 <b>Model usage (process)</b>\n{thin_sep()}\n"
        f"\U0001f4ac Chat calls: <b>{snap.get('total_calls', 0):,}</b> | "
        f"Tokens: <b>{snap.get('total_tokens', 0):,}</b> | "
        f"\u23f1\ufe0f Avg: <b>{avg_latency:.0f}ms</b>\n"
        f"\U0001f3af {tier_line}\n"
        f"\U0001f4e6 {top_models}"
    )


async def _fetch_credit_block() -> str:
    """Live OpenRouter credit line for /admin and /credits (fail-open)."""
    try:
        from rugmunchbot.openrouter_credit import (
            fetch_openrouter_credit,
            format_credit_summary,
        )

        return format_credit_summary(await fetch_openrouter_credit())
    except Exception as exc:
        logger.debug("openrouter credit block failed: %s", exc)
        return "\u26a0\ufe0f OpenRouter credit check unavailable"
