"""Entrance gate: score the join, then hold it or send it a captcha.

Order matters. A join is scored by :mod:entrance first; a risky join is
quarantined for a human (:mod:quarantine) instead of entering the captcha
flow — a raid bot that never taps a button still occupies the room.
Everyone else gets the captcha from :mod:contain, and the join feeds the
raid detector.

A captcha is a *pending challenge*: a (chat, user) entry with a deadline,
mirrored into the durable snapshot. A callback is only honoured while that
entry is live, so a forged or stale button cannot unmute anybody. Entries
are restored and re-armed on restart (:func:`recover`, called by register).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from rugmunchbot.moderation import (
    contain,
    entrance,
    guard_settings,
    persist,
    quarantine,
    raidmode,
    state,
    timeout,
    trust,
)
from rugmunchbot.moderation.config import load_chat_policy
from rugmunchbot.moderation.contain import CAPTCHA_PREFIX, callback_data, parse_callback
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.captcha")

_pending: dict[tuple[int, int], float] = {}


def _key(chat_id: int, user_id: int) -> str:
    return f"{chat_id}:{user_id}"


def _save() -> None:
    persist.save(
        "gate", {_key(chat_id, user_id): at for (chat_id, user_id), at in _pending.items()}
    )


def register_pending(
    chat_id: int, user_id: int, timeout_s: float, *, now: float | None = None
) -> float:
    """Mark this member as awaiting a captcha until ``now + timeout_s``."""
    deadline = (time.time() if now is None else now) + float(timeout_s)
    _pending[(chat_id, user_id)] = deadline
    _save()
    return deadline


def has_pending(chat_id: int, user_id: int, *, now: float | None = None) -> bool:
    """True while a challenge is live for this member (deadline not passed)."""
    deadline = _pending.get((chat_id, user_id))
    if deadline is None:
        return False
    return deadline > (time.time() if now is None else now)


def forget_pending(chat_id: int, user_id: int) -> None:
    if _pending.pop((chat_id, user_id), None) is not None:
        _save()


def reset() -> None:
    _pending.clear()


def recover(app: Any) -> int:
    """Restore unexpired gate pendings after a restart and re-arm timeouts.

    An expired entry is dropped, not enforced: the mute already carries a
    Telegram ``until_date`` backstop, so the gate closed itself while the
    process was down. Returns the number of pendings re-armed.
    """
    raw = persist.load().get("gate") or {}
    now = time.time()
    queue = getattr(app, "job_queue", None)
    bot = getattr(app, "bot", None)
    restored = 0
    for key, deadline in raw.items():
        try:
            chat_id, user_id = (int(part) for part in str(key).split(":", 1))
            remaining = float(deadline) - now
        except (AttributeError, TypeError, ValueError):
            continue
        if remaining <= 0:
            continue
        _pending[(chat_id, user_id)] = float(deadline)
        policy = load_chat_policy(chat_id)
        if queue is not None and policy is not None and policy.new_member_captcha:
            timeout.schedule(
                SimpleNamespace(bot=bot, job_queue=queue), chat_id, user_id, policy, delay=remaining
            )
        restored += 1
    return restored


async def _gate_one(update: Any, context: Any, member: Any, chat: Any, policy: Any) -> None:
    """Score one join, then hold it or send it through the captcha."""
    settings = guard_settings.for_chat(chat.id)
    display_name = getattr(member, "full_name", "") or getattr(member, "first_name", "")
    username = getattr(member, "username", "") or ""
    state.mark_member(chat.id, member.id)
    risk = entrance.assess_join(
        user_id=member.id,
        username=username,
        display_name=display_name,
        is_bot=bool(getattr(member, "is_bot", False)),
        settings=settings,
    )
    if risk.action == "quarantine" and not trust.is_trusted(chat.id, member.id):
        await quarantine.hold(
            context.bot, chat.id, member.id, risk, settings, policy=policy, username=username
        )
        return
    if getattr(member, "is_bot", False):
        return
    contain.record(
        chat.id,
        member.id,
        "new_member_gate",
        "mute" if policy.new_member_captcha else "welcome",
        f"{policy.captcha_type} captcha, {policy.captcha_timeout_s}s"
        if policy.new_member_captcha
        else "welcome only",
        policy.dry_run,
    )
    if not policy.dry_run:
        await _gate_member(context, chat.id, member.id, policy)
    timeout.schedule(context, chat.id, member.id, policy)


async def _gate_member(context: Any, chat_id: int, user_id: int, policy: Any) -> None:
    """Mute, register the pending challenge, then show the prompt."""
    error = await contain.mute(context.bot, chat_id, user_id)
    if error:
        contain.record(
            chat_id, user_id, "captcha_mute_failed", "escalate", f"restrict: {error}", False
        )
        return
    register_pending(chat_id, user_id, policy.captcha_timeout_s)
    if not await contain.prompt(context.bot, chat_id, user_id, policy):
        contain.record(
            chat_id, user_id, "captcha_prompt_failed", "escalate", "challenge not delivered", False
        )


async def handle_new_members(update: Any, context: Any) -> bool:
    """Gate every newcomer. Returns True when the update was consumed."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.new_chat_members:
        return False
    policy = load_chat_policy(chat.id)
    if policy is None:
        return False
    settings = guard_settings.for_chat(chat.id)
    signal = raidmode.note_join(chat.id, settings, dry_run=policy.dry_run)
    if signal.triggered:
        contain.record(chat.id, 0, "raid_detected", "lockdown", signal.reason, policy.dry_run)
    for member in message.new_chat_members:
        if trust.is_trusted(chat.id, member.id):
            state.mark_verified(chat.id, member.id)
        await _gate_one(update, context, member, chat, policy)
    return True


__all__ = [
    "CAPTCHA_PREFIX",
    "callback_data",
    "forget_pending",
    "handle_new_members",
    "has_pending",
    "parse_callback",
    "recover",
    "register_pending",
    "reset",
]
