"""Admin commands for group moderation (/modstatus … /modpardon).

Mirrors the other handler modules: one ``register_*`` function that ``_entry.py``
calls. The engine lives in ``application.bot_data`` so strikes, trust and the
audit trail survive across updates without module-level globals.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any

from telegram import ChatPermissions, Update
from telegram.error import TelegramError
from telegram.ext import CommandHandler, ContextTypes

from rugmunchbot.community.moderation import ModerationEngine

__all__ = ["ENGINE_KEY", "PtbModerationClient", "register_moderation_commands"]

ENGINE_KEY = "moderation_engine"
DENIED = "⛔ Admin only — a chat administrator must run this."
Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE, ModerationEngine], Coroutine[Any, Any, None]]


class PtbModerationClient:
    """Adapts a python-telegram-bot ``Bot`` to the engine's client protocol."""

    def __init__(self, bot: Any) -> None:
        self._bot = bot

    async def delete_message(self, chat_id: int, message_id: int) -> Any:
        return await self._bot.delete_message(chat_id, message_id)

    async def restrict_member(self, chat_id: int, user_id: int, until_date: int) -> Any:
        return await self._bot.restrict_chat_member(
            chat_id,
            user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until_date,
        )

    async def ban_member(self, chat_id: int, user_id: int) -> Any:
        return await self._bot.ban_chat_member(chat_id, user_id)

    async def send_message(self, chat_id: int, text: str) -> Any:
        return await self._bot.send_message(chat_id, text)


def _engine(context: ContextTypes.DEFAULT_TYPE) -> ModerationEngine:
    store = context.application.bot_data
    if ENGINE_KEY not in store:
        store[ENGINE_KEY] = ModerationEngine()
    return store[ENGINE_KEY]


async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    engine = _engine(context)
    if user is not None and engine.is_admin(user.id):
        return True
    try:
        admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    except TelegramError:
        return False
    return any(member.user.id == (user.id if user else 0) for member in admins)


def _target(update: Update, args: list[str]) -> tuple[int, str] | None:
    reply = update.effective_message.reply_to_message if update.effective_message else None
    if reply is not None and reply.from_user is not None:
        return reply.from_user.id, " ".join(args)
    if args and args[0].lstrip("-").isdigit():
        return int(args[0]), " ".join(args[1:])
    return None


async def _reply(update: Update, text: str) -> None:
    await update.effective_message.reply_text(text)


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE, engine: ModerationEngine):
    state = engine.status(update.effective_chat.id)["summary"]
    mode = "DRY-RUN (nothing is enforced)" if state["dry_run"] else "LIVE"
    await _reply(
        update,
        f"🛡 Moderation — {mode}\n"
        f"warn → mute at {state['mute_after']} strikes · ban at {state['ban_after']}\n"
        f"auto-ban: {'on' if state['auto_ban'] else 'off'} · mute {state['mute_seconds']}s\n"
        f"trusted: {state['trusted']} · admins: {state['admins']}",
    )


async def _cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE, engine: ModerationEngine):
    p = engine.policy
    await _reply(
        update,
        "📜 Moderation rules\n"
        "• no phishing / drainer links or shortened URLs\n"
        "• no pump shilling, presales or guaranteed-profit posts\n"
        f"• no wallet-address spam · max {p.max_mentions} mentions per message\n"
        f"• no repeated identical messages ({p.duplicate_threshold}x in {p.duplicate_window_s}s)\n"
        f"• warnings escalate: mute at {p.strikes_mute}, ban at {p.strikes_ban}\n"
        "Appeal to a chat administrator — /modpardon lifts a strike.",
    )


async def _cmd_actions(
    update: Update, context: ContextTypes.DEFAULT_TYPE, engine: ModerationEngine
):
    rows = engine.actions(update.effective_chat.id, limit=10)
    if not rows:
        await _reply(update, "No moderation actions recorded yet.")
        return
    lines = [f"🧾 Last {len(rows)} moderation actions"]
    for row in rows:
        lines.append(
            f"• {row['level']} user {row['user_id']} — {','.join(row['reasons'])}"
            f"{' (dry-run)' if row['dry_run'] else ''}"
        )
    await _reply(update, "\n".join(lines))


async def _cmd_target(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    engine: ModerationEngine,
    *,
    action: str,
):
    parsed = _target(update, list(context.args or []))
    if parsed is None:
        await _reply(update, f"Usage: /{action} <user_id> [reason] — or reply to the member.")
        return
    user_id, reason = parsed
    message_id = getattr(update.effective_message, "message_id", 0)
    outcome = await engine.enforce_manual(
        action,
        update.effective_chat.id,
        user_id,
        PtbModerationClient(context.bot),
        reason=reason,
        message_id=message_id,
    )
    detail = f" ({outcome})" if outcome == "dry_run" else ""
    await _reply(update, f"✅ /{action} → user {user_id}{detail}")


def _wrap(fn: Handler, *, admin: bool):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if admin and not await _is_admin(update, context):
            await _reply(update, DENIED)
            return
        await fn(update, context, _engine(context))

    return handler


def register_moderation_commands(app: Any) -> None:
    """Attach /modstatus … /modpardon to an existing PTB Application."""
    specs: dict[str, tuple[bool, Handler]] = {
        "modstatus": (False, _cmd_status),
        "modrules": (False, _cmd_rules),
        "modactions": (True, _cmd_actions),
        "warn": (True, partial(_cmd_target, action="warn")),
        "mute": (True, partial(_cmd_target, action="mute")),
        "ban": (True, partial(_cmd_target, action="ban")),
        "trust": (True, partial(_cmd_target, action="trust")),
        "untrust": (True, partial(_cmd_target, action="untrust")),
        "modpardon": (True, partial(_cmd_target, action="pardon")),
    }
    for name, (admin, fn) in specs.items():
        app.add_handler(CommandHandler(name, _wrap(fn, admin=admin)), group=0)
