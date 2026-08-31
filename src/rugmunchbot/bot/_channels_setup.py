"""Channel housekeeping — descriptions + admin verification at startup.

Two fail-soft startup lanes (called from ``bot._profile.setup_bot_profile``):

* :func:`set_channel_descriptions` — sets an accurate, branded description
  on every channel the bot administers via Telegram ``setChatDescription``.
  Idempotent: the current description is fetched first and the API is only
  called when it differs.
* :func:`verify_channel_admin` — verifies the bot is an administrator on
  every network channel and logs a WARNING (never crashes) when not.

Both lanes never raise: a Telegram error on one channel logs and the loop
continues to the next channel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application

logger = get_logger("rmi_bot.channels_setup")

#: Branded channel descriptions (role → description). The bot must be an
#: admin on a channel for ``setChatDescription`` to succeed; failures are
#: logged, never raised.
CHANNEL_DESCRIPTIONS: dict[str, str] = {
    "main": (
        "Crypto intelligence, scam detection, and market commentary by RugMunch. "
        "Real alerts, real analysis, no fluff."
    ),
    "updates": (
        "Product updates and release notes. New features, fixes, chains added. "
        "For power users who want to know what changed under the hood."
    ),
    "news": (
        "Breaking crypto news from 200+ sources. Automated 24/7. No curation, "
        "no bias, just the signal. Posts: Every 5 minutes."
    ),
    "scans": (
        "The sketchiest tokens our scanner found today. Comedy + utility. Learn "
        "what a rug looks like before you get rugged. Scan yours free: "
        "rugmunch.io/scanner"
    ),
    "alpha": (
        "Whale wallet movements and smart money signals. Free during beta. See "
        "what the big money is doing before it moves the market."
    ),
    "alerts": (
        "Real-time security alerts. Hacks, exploits, rug pulls, and critical "
        "token warnings. Don't mute this one. Posts: When shit goes down."
    ),
}


async def set_channel_descriptions(app: Application[Any, Any, Any, Any, Any, Any]) -> int:
    """Set the branded description on every network channel (idempotent).

    Skips channels whose description already matches (no API write). Returns
    the number of descriptions actually updated. Never raises.
    """
    from rugmunchbot.config import CHANNELS

    bot = app.bot
    updated = 0
    for role, description in CHANNEL_DESCRIPTIONS.items():
        chat_id = _channel_id(role)
        if not chat_id:
            logger.debug("channel_descriptions:no_chat_id role=%s", role)
            continue
        try:
            chat = await bot.get_chat(chat_id)
            current = str(getattr(chat, "description", "") or "")
            if current.strip() == description.strip():
                logger.debug("channel_descriptions:unchanged role=%s", role)
                continue
            await bot.set_chat_description(chat_id, description=description)
            updated += 1
            logger.info("channel_descriptions:set role=%s", role)
        except Exception as exc:
            # Not-admin / transient API error — log, continue, retry next boot.
            logger.warning("channel_descriptions:failed role=%s err=%s", role, str(exc)[:140])
    logger.info("channel_descriptions:done updated=%d/%d", updated, len(CHANNEL_DESCRIPTIONS))
    return updated


async def verify_channel_admin(app: Application[Any, Any, Any, Any, Any, Any]) -> list[str]:
    """Verify the bot is admin on every network channel; warn when not.

    Returns the list of roles where the bot is NOT an administrator (useful
    for tests/ops). Never raises — per-channel failures count as "not admin"
    and are logged.
    """
    bot = app.bot
    not_admin: list[str] = []
    for role in CHANNEL_DESCRIPTIONS:
        chat_id = _channel_id(role)
        if not chat_id:
            not_admin.append(role)
            logger.warning("channel_admin_check:no_chat_id role=%s", role)
            continue
        try:
            member = await bot.get_chat_member(chat_id, bot.id)
            status = str(getattr(member, "status", "") or "")
            if status not in ("administrator", "creator"):
                not_admin.append(role)
                logger.warning(
                    "channel_admin_check:NOT_ADMIN role=%s status=%s — post delivery "
                    "to this channel will fail until the bot is promoted",
                    role,
                    status,
                )
        except Exception as exc:
            not_admin.append(role)
            logger.warning("channel_admin_check:failed role=%s err=%s", role, str(exc)[:140])
    if not_admin:
        logger.warning("channel_admin_check:summary not_admin=%s", ",".join(not_admin))
    else:
        logger.info("channel_admin_check:ok all %d channels admin", len(CHANNEL_DESCRIPTIONS))
    return not_admin


def _channel_id(role: str) -> str | None:
    """Resolve a channel chat id (fail-soft — unknown role → None)."""
    try:
        from rugmunchbot.config._channels import _resolve_channel_id

        return _resolve_channel_id(role)
    except Exception:
        return None
