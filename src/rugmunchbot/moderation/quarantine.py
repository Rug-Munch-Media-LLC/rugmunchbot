"""Hold a suspicious join outside the conversation until a human decides.

Quarantine is a HOLD, not a punishment: the member is restricted (cannot post),
the decision is written to the audit log with a stable action id, and an
optional quarantine channel gets the full explanation plus the one command that
releases them. Nothing is silently banned; every hold is reversible by
/modrelease and every release is audited.

Dry-run chats record the decision and change nothing (same contract as the rest
of the moderation layer).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from rugmunchbot.moderation import audit, contain, persist
from rugmunchbot.moderation.config import load_chat_policy
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from rugmunchbot.moderation.entrance import JoinRisk
    from rugmunchbot.moderation.guard_settings import GuardSettings

logger = get_logger("rmi_bot.moderation.quarantine")

MAX_PENDING = 500
_pending: dict[int, dict[int, dict[str, Any]]] = {}


def _entry(
    action_id: str, chat_id: int, user_id: int, risk: JoinRisk, dry_run: bool
) -> dict[str, Any]:
    return audit.build_entry(
        action_id=action_id,
        chat_id=chat_id,
        chat_title="",
        user_id=user_id,
        username="",
        rule="entrance_quarantine",
        action="quarantine",
        reason=risk.explained,
        matched="",
        dry_run=dry_run,
        applied=False,
        actor="auto:entrance",
        extra={"score": risk.score, "signals": list(risk.reasons)},
    )


async def hold(
    bot: Any,
    chat_id: int,
    user_id: int,
    risk: JoinRisk,
    settings: GuardSettings,
    *,
    policy: Any = None,
    username: str = "",
) -> dict[str, Any]:
    """Restrict the join, record why, and make the release path discoverable."""
    chat_policy = policy or load_chat_policy(chat_id)
    dry_run = True if chat_policy is None else chat_policy.dry_run
    action_id = audit.new_action_id()
    entry = _entry(action_id, chat_id, user_id, risk, dry_run)
    entry["username"] = username
    contain.write(entry)

    bucket = _pending.setdefault(chat_id, {})
    if len(bucket) >= MAX_PENDING:
        bucket.pop(next(iter(bucket)))
    until = datetime.now(UTC) + timedelta(minutes=settings.quarantine_mute_minutes)
    bucket[user_id] = {
        "user_id": user_id,
        "action_id": action_id,
        "score": risk.score,
        "reasons": list(risk.reasons),
        "until": until.isoformat(timespec="seconds"),
    }
    _save()
    if dry_run:
        return entry

    errors: list[str] = []
    error = await contain.mute(bot, chat_id, user_id, until=until)
    if error:
        errors.append(f"restrict: {error}")
        await contain.escalate(
            bot,
            chat_id,
            user_id,
            "entrance_quarantine_failed",
            f"quarantine could not mute: {error}",
            action_id=action_id,
            target=settings.quarantine_channel_id,
        )
    if settings.quarantine_channel_id:
        text = (
            f"🛡️ Quarantined user {user_id} ({username or 'no username'})\n"
            f"score {risk.score}: {risk.explained}\n"
            f"action_id {action_id} · release: /modrelease {user_id}"
        )
        try:
            await bot.send_message(chat_id=settings.quarantine_channel_id, text=text[:900])
        except Exception as exc:
            errors.append(f"channel: {type(exc).__name__}")
    entry["applied"] = not errors
    entry["error"] = "; ".join(errors) or None
    if errors:
        logger.warning("quarantine not fully applied", extra={"chat_id": chat_id})
    return entry


async def release(
    bot: Any,
    chat_id: int,
    user_id: int,
    *,
    actor: str,
    reason: str = "",
    dry_run: bool = False,
) -> dict[str, Any] | None:
    """Lift a hold and audit who released it and why. None when not pending."""
    row = _pending.get(chat_id, {}).pop(user_id, None)
    if row is None:
        return None
    errors: list[str] = []
    if not dry_run:
        try:
            from telegram import ChatPermissions

            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=True),
                use_independent_chat_permissions=True,
            )
        except Exception as exc:
            errors.append(f"release: {type(exc).__name__}")
    entry = audit.build_entry(
        action_id=audit.new_action_id(),
        chat_id=chat_id,
        chat_title="",
        user_id=user_id,
        username="",
        rule="quarantine_release",
        action="release",
        reason=reason or "human release",
        matched="",
        dry_run=dry_run,
        applied=not errors,
        actor=actor,
        error="; ".join(errors) or None,
        extra={"held_action_id": row["action_id"], "score": row["score"]},
    )
    _save()
    contain.write(entry)
    return entry


def _save() -> None:
    """Mirror the pending holds into the durable snapshot (best-effort)."""
    persist.save_ids("quarantine", _pending)


def recover() -> int:
    """Restore holds recorded before a restart. Returns the number of chats."""
    rows = persist.restore_ids("quarantine")
    _pending.clear()
    _pending.update(rows)
    return len(rows)


def pending(chat_id: int) -> list[dict[str, Any]]:
    """Current holds for the chat, oldest first (admin view / tests)."""
    return list(_pending.get(chat_id, {}).values())


def reset() -> None:
    _pending.clear()


__all__ = ["hold", "pending", "recover", "release", "reset"]
