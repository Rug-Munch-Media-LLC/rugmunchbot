"""Modlog delivery: mirror moderation decisions into a chat's log channel.

Heimdall-parity "admin alerts": every applied action (and every ``alert``
decision such as a raid) is mirrored into a log destination so admins see what
the bot did without reading JSONL on the server.

Resolution order for the destination — explicit first, never a guess:

1. ``community.modlog_chat_id`` in the chat's config block (operator opt-in),
2. the chat's linked discussion channel (only when ``modlog_linked`` is on).

Delivery is opt-in (``community.modlog_enabled`` is ``False`` by default) and
strictly best-effort: any Telegram error is logged and swallowed, so a missing
right or a deleted log channel can never raise into the dispatcher or block the
moderation action itself.
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.modlog")

MAX_REASON = 300


def _esc(value: Any) -> str:
    """HTML-escape one dynamic value (Telegram parse_mode=HTML)."""
    return html.escape(str(value if value is not None else ""))


def _who(entry: dict[str, Any]) -> str:
    """Render the subject of an entry as ``id (@handle)``."""
    user_id = entry.get("user_id") or 0
    username = str(entry.get("username") or "")
    return f"<code>{user_id}</code>" + (f" (@{_esc(username)})" if username else "")


def format_entry(entry: dict[str, Any], title: str = "") -> str:
    """One modlog message for one audit record. Pure, no I/O."""
    action = str(entry.get("action") or "action").upper()
    rule = str(entry.get("rule") or "")
    state = "APPLIED" if entry.get("applied") else "DRY-RUN"
    reason = str(entry.get("reason") or "")[:MAX_REASON]
    lines = [
        f"🛡 <b>{_esc(action)}</b> · {state}",
        f"member: {_who(entry)}",
        f"rule: <code>{_esc(rule)}</code>"
        + (
            f" · matched: <code>{_esc(entry.get('matched'))[:120]}</code>"
            if entry.get("matched")
            else ""
        ),
    ]
    if reason:
        lines.append(f"reason: {_esc(reason)}")
    if entry.get("actor"):
        lines.append(f"actor: {_esc(entry.get('actor'))}")
    if title:
        lines.append(f"chat: {_esc(title)} · <code>{entry.get('chat_id')}</code>")
    if entry.get("error"):
        lines.append(f"⚠️ error: {_esc(str(entry.get('error'))[:160])}")
    lines.append(f"id: <code>{_esc(entry.get('action_id'))}</code>")
    return "\n".join(lines)


async def _target(bot: Any, chat_id: int, cfg: Any) -> int | None:
    """Resolve the log destination, or None when nothing is configured."""
    explicit = int(getattr(cfg, "modlog_chat_id", 0) or 0)
    if explicit:
        return explicit
    if not getattr(cfg, "modlog_linked", False):
        return None
    try:
        chat = await bot.get_chat(chat_id)
    except Exception:
        logger.debug("modlog: linked chat lookup failed", exc_info=True)
        return None
    linked = getattr(chat, "linked_chat_id", None)
    return int(linked) if linked else None


async def deliver(
    bot: Any, entry: dict[str, Any], *, chat_id: int, cfg: Any, title: str = ""
) -> bool:
    """Send *entry* to the chat's modlog. True when Telegram accepted it.

    Never raises: moderation must not depend on the log channel being healthy.
    """
    if not getattr(cfg, "modlog_enabled", False):
        return False
    dest = await _target(bot, chat_id, cfg)
    if dest is None:
        return False
    try:
        await bot.send_message(
            chat_id=dest,
            text=format_entry(entry, title=title),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return True
    except Exception:
        logger.info("modlog delivery failed", extra={"dest": dest}, exc_info=True)
        return False


__all__ = ["deliver", "format_entry"]
