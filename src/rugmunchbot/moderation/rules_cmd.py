"""Per-chat rules: ``/rules`` (read) and ``/setrules`` (admin write).

The rule text is a per-chat override stored in the community store (same
bucket as the welcome text), never written back into ``configs/moderation.json``:
the JSON stays the operator's opt-in ceiling and the bot never rewrites its own
config file. Writing needs ``community.rules_enabled`` — flipping that flag
stays a deliberate operator action.

``/rules`` falls back to a generated summary of the enforced policy, so a chat
always has something to show even before an admin writes custom text.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.moderation import audit, auth, chatconf, store

KIND = "settings"
FIELD = "rules_text"
MAX_RULES = chatconf.MAX_TEXT
USAGE = "Usage: /setrules <text>  ·  /clearrules to go back to the built-in summary"


def builtin_text(policy: Any) -> str:
    """Fallback rules text derived from the effective policy."""
    if policy is None:
        # Member-facing: never leak the operator's config path or the fact that
        # enforcement is off — that is an invitation to spam.
        return (
            "📜 <b>Chat rules</b>\n"
            "• be respectful · no spam, no shilling, no phishing links\n"
            "• no unsolicited DMs to members\n"
            "Report a message with /report (reply to it)."
        )
    mode = "observing only (dry-run)" if policy.dry_run else "enforcing"
    return (
        "📜 <b>Chat rules</b>\n"
        "• no phishing, drainer or shortened links\n"
        "• no shilling, presales or guaranteed-profit posts\n"
        "• no invite links, wallet-address spam or repeated messages\n"
        f"• warnings escalate to a mute after {policy.dup_count} repeats\n"
        f"Current mode: {mode}\n"
        "Report a message with /report (reply to it). Appeal with /appeal."
    )


def _record(update: Any, ok: bool, reason: str) -> None:
    user, chat = update.effective_user, update.effective_chat
    audit.record(
        audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=chat.id if chat else 0,
            chat_title=getattr(chat, "title", "") or "",
            user_id=user.id if user else 0,
            username=getattr(user, "username", "") or "",
            rule="community_config",
            action="set",
            reason=reason,
            matched=FIELD,
            dry_run=False,
            applied=ok,
            actor=f"admin:{user.id}" if user else "auto",
        )
    )


async def cmd_rules(update: Any, context: Any) -> None:
    """``/rules`` — show this chat's rules (custom text or the built-in summary)."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None:
        return
    from rugmunchbot.moderation.config import load_chat_policy

    cfg = await chatconf.effective(chat.id)
    text = cfg.rules_text.strip() if cfg.rules_text else builtin_text(load_chat_policy(chat.id))
    await message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def cmd_setrules(update: Any, context: Any) -> None:
    """``/setrules <text>`` — admins store a custom rules message."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None or not await auth.require_admin(update, context):
        return
    parts = (message.text or "").split(maxsplit=1)
    body = parts[1].strip()[:MAX_RULES] if len(parts) > 1 else ""
    if not body:
        await message.reply_text(USAGE)
        return
    if not chatconf.load_community_config(chat.id).rules_enabled:
        await message.reply_text(
            "Custom rules are disabled for this chat; an operator must enable "
            "community.rules_enabled in configs/moderation.json first."
        )
        return
    ok = await store.set_field(store.chat_key(chat.id, KIND), FIELD, body)
    _record(update, ok, body[:80])
    await message.reply_text("✅ Rules saved." if ok else "⚠️ Save failed.")


async def cmd_clearrules(update: Any, context: Any) -> None:
    """``/clearrules`` — admins drop the override and restore the summary."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None or not await auth.require_admin(update, context):
        return
    removed = await store.del_field(store.chat_key(chat.id, KIND), FIELD)
    _record(update, removed, "clear rules_text")
    await message.reply_text(
        "✅ Custom rules removed." if removed else "Nothing to clear — using the built-in summary."
    )


__all__ = ["builtin_text", "cmd_clearrules", "cmd_rules", "cmd_setrules"]
