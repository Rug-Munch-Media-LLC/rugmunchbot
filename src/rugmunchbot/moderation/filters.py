"""Keyword auto-replies (filters): ``/filter``, ``/filters``, ``/stopfilter``.

A filter is ``trigger -> reply`` per chat, matched case-insensitively on the
lower-cased message text. Replies are capped per user per minute so a filter can
never be used to flood; commands never trigger one. Off by default
(``community.filters_enabled``).
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot.moderation import audit, auth, chatconf, store

MAX_TRIGGER = 32
MAX_REPLY = 400
MAX_FILTERS = 100
KIND = "filters"

_hits: dict[tuple[int, int], list[float]] = {}


def normalise(trigger: str) -> str | None:
    """Lower-case, whitespace-collapsed trigger, or None when unusable."""
    text = " ".join((trigger or "").lower().split())
    return text[:MAX_TRIGGER] if len(text) >= 2 else None


def _throttled(chat_id: int, user_id: int, limit: int, now: float | None = None) -> bool:
    ts = time.monotonic() if now is None else now
    bucket = [t for t in _hits.get((chat_id, user_id), []) if ts - t < 60]
    if len(bucket) >= limit:
        _hits[(chat_id, user_id)] = bucket
        return True
    bucket.append(ts)
    _hits[(chat_id, user_id)] = bucket
    return False


def match(text: str, filters: dict[str, str]) -> str | None:
    """Return the reply for the first trigger contained in *text*."""
    haystack = " ".join((text or "").lower().split())
    for trigger in sorted(filters, key=len, reverse=True):
        if trigger and trigger in haystack:
            return filters[trigger]
    return None


async def maybe_reply(update: Any, ctx: Any, cfg: chatconf.CommunityConfig) -> bool:
    """Answer a filtered message once. Returns True when a reply was sent."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None or not cfg.filters_enabled or not ctx.text:
        return False
    filters = await store.all_fields(store.chat_key(chat.id, KIND))
    reply = match(ctx.text, filters)
    if reply is None or _throttled(chat.id, ctx.user_id, cfg.filter_replies_per_min):
        return False
    try:
        await message.reply_text(reply[:MAX_REPLY], disable_web_page_preview=True)
    except Exception:  # a failed auto-reply must not break the gate
        return False
    return True


async def cmd_filter(update: Any, context: Any) -> None:
    """``/filter <trigger> <reply>`` — add or replace one filter (admins)."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None or not await auth.require_admin(update, context):
        return
    args = message.text.split(maxsplit=2) if message.text else []
    if len(args) < 3:
        await message.reply_text("Usage: /filter <trigger> <reply text>")
        return
    trigger = normalise(args[1])
    if trigger is None:
        await message.reply_text("Trigger must be at least 2 characters.")
        return
    ok = await store.set_field(
        store.chat_key(chat.id, KIND), trigger, args[2].strip()[:MAX_REPLY], max_fields=MAX_FILTERS
    )
    audit.record(
        audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=chat.id,
            chat_title="",
            user_id=update.effective_user.id,
            username=update.effective_user.username or "",
            rule="filter_set",
            action="filter",
            reason="auto-reply",
            matched=trigger,
            dry_run=False,
            applied=ok,
            actor=f"admin:{update.effective_user.id}",
        )
    )
    await message.reply_text(
        f"✅ Filter <b>{trigger}</b> saved." if ok else "⚠️ Filter limit reached.",
        parse_mode="HTML",
    )


async def cmd_filters(update: Any, context: Any) -> None:
    """``/filters`` — list this chat's triggers (open to members)."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None:
        return
    cfg = chatconf.load_community_config(chat.id)
    names = sorted(await store.all_fields(store.chat_key(chat.id, KIND)))
    state = "on" if cfg.filters_enabled else "off (operator must enable)"
    if not names:
        await message.reply_text(f"Filters {state}. No triggers configured.")
        return
    await message.reply_text(
        f"🔎 <b>Filters</b> ({state}): " + ", ".join(names[:MAX_FILTERS]), parse_mode="HTML"
    )


async def cmd_stopfilter(update: Any, context: Any) -> None:
    """``/stopfilter <trigger>|all`` — remove one filter or every filter (admins)."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None or not await auth.require_admin(update, context):
        return
    raw = (context.args or [""])[0].lower()
    key = store.chat_key(chat.id, KIND)
    if raw == "all":
        removed = 0
        for trigger in await store.all_fields(key):
            removed += int(await store.del_field(key, trigger))
        await message.reply_text(f"🗑️ Removed {removed} filter(s).")
        return
    trigger = normalise(raw)
    if trigger is None:
        await message.reply_text("Usage: /stopfilter <trigger>|all")
        return
    ok = await store.del_field(key, trigger)
    await message.reply_text(
        f"🗑️ Removed <b>{trigger}</b>." if ok else f"No filter <b>{trigger}</b>.",
        parse_mode="HTML",
    )


__all__ = ["cmd_filter", "cmd_filters", "cmd_stopfilter", "match", "maybe_reply", "normalise"]
