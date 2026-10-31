"""Telegram containment primitives: restrict, challenge, audit row.

``captcha`` decides who is gated; this module is the only place that
*changes* a member's ability to speak, so the fail-closed rules live here:

* ``mute`` retries once (without the independent-permissions flag) and
  reports the failure instead of pretending the member is held;
* ``prompt`` reports whether the challenge reached the room — a muted
  member with no visible challenge is audited, never silent;
* ``record`` never invents a safe audit write: an ``OSError`` is logged at
  error level and reported as ``False`` to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rugmunchbot.moderation import audit, challenge, messages
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.contain")

CAPTCHA_PREFIX = "modcap"
BUTTON_LABEL = "I'm human ✅"


def callback_data(chat_id: int, user_id: int, answer: int | None = None) -> str:
    base = f"{CAPTCHA_PREFIX}:{chat_id}:{user_id}"
    return base if answer is None else f"{base}:{answer}"


def parse_callback(data: str) -> tuple[int, int, int | None] | None:
    """Parse callback data; None for anything that is not one of our buttons."""
    parts = (data or "").split(":")
    if len(parts) not in {3, 4} or parts[0] != CAPTCHA_PREFIX:
        return None
    try:
        answer = int(parts[3]) if len(parts) == 4 else None
        return int(parts[1]), int(parts[2]), answer
    except ValueError:
        return None


def record(
    chat_id: int,
    user_id: int,
    rule: str,
    action: str,
    reason: str,
    dry_run: bool,
    *,
    applied: bool = False,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Write one gate audit row. False when the write failed (logged loud)."""
    entry = audit.build_entry(
        action_id=audit.new_action_id(),
        chat_id=chat_id,
        chat_title="",
        user_id=user_id,
        username="",
        rule=rule,
        action=action,
        reason=reason,
        matched="",
        dry_run=dry_run,
        applied=applied,
        actor="auto:entrance",
        extra=extra,
    )
    try:
        audit.record(entry)
    except OSError:
        logger.error("gate audit write failed", extra={"rule": rule}, exc_info=True)
        return False
    return True


def write(entry: dict[str, Any]) -> bool:
    """Append a row prepared by the caller. False when the trail is unavailable."""
    try:
        audit.record(entry)
    except OSError:
        logger.error(
            "moderation audit write failed", extra={"rule": entry.get("rule")}, exc_info=True
        )
        return False
    return True


@dataclass
class Escalation:
    user_id: int
    rule: str
    reason: str
    action_id: str = ""
    target: int = 0
    dry_run: bool = False


async def escalate(bot: Any, chat_id: int, esc: Escalation) -> bool:
    """A containment action that failed is a security event: audit + tell a human."""
    user_id = esc.user_id
    rule = esc.rule
    reason = esc.reason
    action_id = esc.action_id
    target = esc.target
    dry_run = esc.dry_run
    record(chat_id, user_id, rule, "escalate", reason, dry_run, extra={"action_id": action_id})
    try:
        await bot.send_message(
            chat_id=target or chat_id,
            text=f"⚠️ {reason} · user {user_id} · action_id {action_id} — act manually.",
        )
    except Exception:
        logger.error("escalation notice failed", extra={"rule": rule}, exc_info=True)
        return False
    return True


async def mute(bot: Any, chat_id: int, user_id: int, *, until: Any = None) -> str | None:
    """Restrict a member. Returns the failure type when every attempt failed."""
    from telegram import ChatPermissions

    last: Exception | None = None
    for attempt in range(2):
        try:
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
                use_independent_chat_permissions=attempt == 0,
            )
            return None
        except Exception as exc:
            last = exc
            logger.warning("restrict attempt %s failed", attempt + 1, exc_info=True)
    return "" if last is None else type(last).__name__


async def prompt(bot: Any, chat_id: int, user_id: int, policy: Any) -> bool:
    """Send the captcha keyboard. False when it did not reach the chat."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    minutes = max(1, policy.captcha_timeout_s // 60 or 1)
    if policy.captcha_type == "math":
        question, options, _ = challenge.challenge(chat_id, user_id)
        text = messages.captcha_math(question, minutes)
        row = [
            InlineKeyboardButton(str(option), callback_data=callback_data(chat_id, user_id, option))
            for option in options
        ]
    else:
        text = messages.captcha_prompt(minutes)
        row = [InlineKeyboardButton(BUTTON_LABEL, callback_data=callback_data(chat_id, user_id))]
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([row]),
        )
    except Exception:
        logger.warning("captcha prompt send failed", exc_info=True)
        return False
    return True


__all__ = [
    "CAPTCHA_PREFIX",
    "callback_data",
    "escalate",
    "mute",
    "parse_callback",
    "prompt",
    "record",
    "write",
]
