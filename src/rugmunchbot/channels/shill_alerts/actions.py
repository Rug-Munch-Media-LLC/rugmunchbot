"""Moderation buttons behind an alert card — intent only, admin-gated.

The lane watches *other people's* channels, so a button never bans anyone in
Telegram by itself. It records an operator decision against our own state
(false-positive suppression) or emits a typed intent that the moderation
console consumes. Admin rights are checked here; silently allowing a
non-admin press is a bug, not a convenience.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.channels.shill_alerts import config
from rugmunchbot.channels.shill_alerts.card import CALLBACK_PREFIX
from rugmunchbot.channels.shill_alerts.dedupe import AlertLedger
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.shill_alerts.actions")

VALID_ACTIONS = frozenset({"quarantine_channel", "ban_actor", "mark_false_positive", "escalate"})


def _entity_for_key(key: str, ledger: AlertLedger) -> str:
    for row in ledger.rows():
        if row.get("k") == key and row.get("e"):
            return str(row["e"])
    return ""


def resolve_action(callback_data: str, *, is_admin: bool) -> dict[str, Any]:
    """Parse a ``shill:<action>:<key>`` payload into an executable intent.

    Returns ``{"action", "key", "allowed", "reason"}``. Unknown payloads and
    non-admins are refused with a reason — nothing is silently ignored.
    """
    raw = str(callback_data or "")
    if not raw.startswith(CALLBACK_PREFIX):
        return {"action": "", "key": "", "allowed": False, "reason": "not_a_shill_callback"}
    parts = raw[len(CALLBACK_PREFIX) :].split(":", 1)
    if len(parts) != 2:
        return {"action": "", "key": "", "allowed": False, "reason": "malformed_payload"}
    action, key = parts[0].lower(), parts[1]
    if action not in VALID_ACTIONS:
        return {"action": action, "key": key, "allowed": False, "reason": "unknown_action"}
    if not is_admin:
        return {"action": action, "key": key, "allowed": False, "reason": "admin_only"}
    return {"action": action, "key": key, "allowed": True, "reason": "ok"}


async def _answer(query: Any, text: str) -> None:
    """Answer a callback (already answered by the router — never raise)."""
    try:
        await query.answer(text, show_alert=False)
    except Exception as exc:  # a stale/duplicate answer is not an error
        logger.debug("shill_alerts:answer_failed err=%s", str(exc)[:120])


async def handle_moderation_action(update: Any, ctx: Any) -> None:
    """CallbackQuery handler for the card buttons (registered by the router).

    ``mark_false_positive`` suppresses the entity for the day and is the only
    action applied in-place; the rest are logged as typed intents for the
    moderation console (an operator confirms any Telegram-side ban there).
    """
    query = getattr(update, "callback_query", None)
    if query is None:
        return
    user = getattr(query, "from_user", None)
    is_admin = bool(getattr(user, "id", 0)) in set(_admin_ids(ctx))
    intent = resolve_action(getattr(query, "data", "") or "", is_admin=is_admin)
    if not intent["allowed"]:
        await _answer(query, f"Not allowed: {intent['reason']}")
        return

    ledger = AlertLedger(config.ALERT_LEDGER)
    entity = _entity_for_key(intent["key"], ledger)
    if intent["action"] == "mark_false_positive" and entity:
        ledger.mark_false_positive(entity)
        logger.info(
            "shill_alerts:false_positive entity=%s by=%s", entity[:16], getattr(user, "id", 0)
        )
        await _answer(query, "Marked false positive for today")
        return
    logger.info(
        "shill_alerts:moderation_intent action=%s entity=%s by=%s",
        intent["action"],
        entity[:16],
        getattr(user, "id", 0),
    )
    await _answer(query, f"{intent['action'].replace('_', ' ')} queued for review")


def _admin_ids(ctx: Any) -> tuple[int, ...]:
    """Admin ids from the bot config (empty when unavailable — fails closed)."""
    try:
        from rugmunchbot.config import settings

        ids = getattr(settings, "admin_ids", None) or ()
        return tuple(int(i) for i in ids)
    except Exception:
        return ()
