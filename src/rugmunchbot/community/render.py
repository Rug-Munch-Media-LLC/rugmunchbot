"""HTML rendering for the ``/setup`` surface.

All user-supplied values are escaped here — the handler replies with
``parse_mode=HTML``, so an unescaped symbol or label would be an injection
point into Telegram's markup parser.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rugmunchbot.community.audit import AuditEntry
    from rugmunchbot.community.config_models import CommunitySettings

SEPARATOR = "─" * 20


def summary_text(settings: CommunitySettings) -> str:
    """One-screen summary of the chat's current configuration."""
    lines = ["⚙️ <b>Community settings</b>", SEPARATOR]
    if settings.token is None:
        lines.append("🪙 <b>Token:</b> <i>not set</i> — /setup token …")
    else:
        token = settings.token
        lines.append(
            f"🪙 <b>{html.escape(token.symbol)}</b> ({html.escape(token.name)}) "
            f"on <code>{token.chain.value}</code>\n"
            f"   <code>{html.escape(token.address)}</code>"
        )
    lines.append(
        "💰 <b>Buy presets:</b> " + _amounts(settings.amounts.buy) + "\n"
        "💰 <b>Sell presets:</b> " + _amounts(settings.amounts.sell)
    )
    lines.append(
        f"🔘 <b>Buttons:</b> {html.escape(settings.emojis.buy.emoji)} "
        f"{html.escape(settings.emojis.buy.label)} · "
        f"{html.escape(settings.emojis.sell.emoji)} "
        f"{html.escape(settings.emojis.sell.label)}"
    )
    moderation = settings.moderation
    lines.append(
        f"🛡️ <b>Moderation:</b> {moderation.max_messages_per_minute} msg/min · "
        f"{moderation.max_links_per_message} links · "
        f"{len(moderation.banned_words)} banned words"
    )
    voting = settings.voting
    lines.append(
        f"🗳️ <b>Voting:</b> {'on' if voting.enabled else 'off'} · "
        f"quorum {voting.quorum_pct}% · {voting.voting_duration_hours}h"
    )
    admins = ", ".join(str(a) for a in settings.admins) or "—"
    lines.append(f"👤 <b>Extra admins:</b> {html.escape(admins)}")
    enabled = [name for name, on in settings.features.model_dump().items() if on]
    lines.append("🚩 <b>Features:</b> " + (", ".join(enabled) or "none"))
    return "\n".join(lines)


def audit_text(entries: list[AuditEntry]) -> str:
    """Most recent settings changes, newest first."""
    if not entries:
        return "🗒️ <b>Audit log</b>\n" + SEPARATOR + "\nNo changes recorded yet."
    lines = ["🗒️ <b>Audit log</b> (newest first)", SEPARATOR]
    for entry in entries:
        lines.append(
            f"• <code>{html.escape(entry.key)}</code> by "
            f"<code>{entry.user_id}</code> → "
            f"<code>{html.escape(_short(entry.new_value))}</code>"
        )
    return "\n".join(lines)


def _amounts(values: list[float]) -> str:
    return ", ".join(f"{value:g}" for value in values) or "—"


def _short(value: object, limit: int = 60) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"
