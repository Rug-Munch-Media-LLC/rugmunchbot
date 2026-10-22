"""Admin + appeal commands for the moderation layer.

Every command is chat-admin, owner or (for /appeal) private-DM only. None of
them acts on Telegram unless the chat's policy is live — except /modpardon,
which is an explicit human override and always honoured.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.config import OWNER_IDS
from rugmunchbot.moderation import audit, messages
from rugmunchbot.moderation.actions import lift
from rugmunchbot.moderation.config import load_chat_policy
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.commands")

_CHAT_TYPES = {"group", "supergroup"}


async def _allowed(update: Any, context: Any) -> bool:
    user, chat = update.effective_user, update.effective_chat
    if user is None or chat is None:
        return False
    if user.id in OWNER_IDS:
        return True
    try:
        from telegram.constants import ChatMemberStatus

        member = await context.bot.get_chat_member(chat_id=chat.id, user_id=user.id)
        return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
    except Exception:
        return False


async def cmd_modstatus(update: Any, context: Any) -> None:
    """Show this chat's effective policy (admins only)."""
    chat = update.effective_chat
    if not await _allowed(update, context) or chat is None:
        await update.effective_message.reply_text("⛔ Chat admins only.")
        return
    policy = load_chat_policy(chat.id)
    if policy is None:
        await update.effective_message.reply_text(
            f"No moderation policy for chat <code>{chat.id}</code>.\n"
            "Add it to configs/moderation.json to enable the guard.",
            parse_mode="HTML",
        )
        return
    mode = "DRY-RUN (nothing is deleted/muted)" if policy.dry_run else "LIVE"
    await update.effective_message.reply_text(
        f"🛡️ <b>Moderation</b> — {mode}\n"
        f"chat: <code>{chat.id}</code>\n"
        f"link allowlist: {len(policy.links_allow)} domains · scam needles: "
        f"{len(policy.scam_domains)} · promo patterns: {len(policy.promo_patterns)}\n"
        f"dup: {policy.dup_count}/{policy.dup_window_s}s → {policy.dup_action} · "
        f"velocity: {policy.user_msgs}/{policy.velocity_window_s}s → {policy.user_velocity_action}\n"
        f"address: score<{policy.address_score} → {policy.address_action}"
        f"{' + delete' if policy.address_delete else ''}\n"
        f"captcha: {'on' if policy.new_member_captcha else 'off'} · exempt ids: "
        f"{len(policy.exempt_user_ids)}",
        parse_mode="HTML",
    )


async def cmd_modactions(update: Any, context: Any) -> None:
    """List the last N recorded decisions for this chat."""
    chat = update.effective_chat
    if not await _allowed(update, context) or chat is None:
        await update.effective_message.reply_text("⛔ Chat admins only.")
        return
    try:
        limit = max(1, min(20, int((context.args or ["5"])[0])))
    except (TypeError, ValueError):
        limit = 5
    rows = audit.tail(chat.id, limit)
    if not rows:
        await update.effective_message.reply_text("No moderation actions recorded here.")
        return
    lines = ["🗂️ <b>Recent moderation</b>"]
    for row in rows:
        flag = "🧪" if row.get("dry_run") else "✅" if row.get("applied") else "⚠️"
        lines.append(
            f"{flag} <code>{row.get('action_id', '')}</code> {row.get('rule')}→{row.get('action')} "
            f"u:{row.get('user_id')} {row.get('reason', '')[:60]}"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_modpardon(update: Any, context: Any) -> None:
    """Lift a mute/ban: /modpardon <user_id> [reason]."""
    chat = update.effective_chat
    if not await _allowed(update, context) or chat is None:
        await update.effective_message.reply_text("⛔ Chat admins only.")
        return
    args = context.args or []
    try:
        user_id = int(args[0])
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Usage: /modpardon <user_id> [reason]")
        return
    policy = load_chat_policy(chat.id)
    if policy is None:
        await update.effective_message.reply_text("No policy for this chat.")
        return
    ok = await lift(context.bot, chat.id, user_id, policy)
    audit.record(
        audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=chat.id,
            chat_title=chat.title or "",
            user_id=user_id,
            username="",
            rule="pardon",
            action="unmute",
            reason=" ".join(args[1:])[:120] or "admin pardon",
            matched="",
            dry_run=policy.dry_run,
            applied=ok,
            actor=f"admin:{update.effective_user.id}",
            error=None if ok else "telegram refused or dry-run",
        )
    )
    await update.effective_message.reply_text(
        f"{'✅ Pardoned' if ok else '🧪 Dry-run: would pardon'} <code>{user_id}</code>",
        parse_mode="HTML",
    )


async def cmd_appeal(update: Any, context: Any) -> None:
    """Private-DM appeal against the last action on this user."""
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return
    if chat.type != "private":
        await update.effective_message.reply_text("Appeals are DM-only: message the bot privately.")
        return
    reason = " ".join(context.args or [])[:400] or "(no reason given)"
    last = None
    for row in audit.tail(None, 200):
        if row.get("user_id") == user.id:
            last = row
            break
    action_id = last.get("action_id") if last else "none"
    audit.record(
        audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=last.get("chat_id", 0) if last else 0,
            chat_title="appeal",
            user_id=user.id,
            username=user.username or "",
            rule="appeal",
            action="appeal",
            reason=reason,
            matched=str(action_id),
            dry_run=False,
            applied=True,
            actor=f"user:{user.id}",
        )
    )
    await update.effective_message.reply_text(
        messages.appeal_receipt(str(action_id)), parse_mode="HTML"
    )


__all__ = ["cmd_appeal", "cmd_modactions", "cmd_modpardon", "cmd_modstatus"]
