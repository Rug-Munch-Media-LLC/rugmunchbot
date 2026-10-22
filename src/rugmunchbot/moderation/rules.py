"""Text rules — the first matching rule wins, each names itself for the audit.

Pure functions over (MsgContext, ChatPolicy, MessageStats): no I/O, no
Telegram, no clock beyond the stats the caller already computed.
"""

from __future__ import annotations

from rugmunchbot.moderation import links, messages
from rugmunchbot.moderation.models import (
    Action,
    MsgContext,
)
from rugmunchbot.moderation.state import MessageStats  # noqa: TC001 — annotation only

# Names that impersonate us. Narrow on purpose: brand + role, never bare
# "admin" (real admins are exempted separately by `exempt_admins`).
_IMPERSONATION = (
    "rugmunch support",
    "rugmunch admin",
    "rugmunch official",
    "rugmunch team",
    "rugmunch help",
    "rmi support",
    "rmi admin",
    "rugmunchbot support",
)


def _is_impostor(ctx: MsgContext) -> bool:
    if ctx.is_admin:
        return False
    blob = links.norm(f"{ctx.display_name} {ctx.username}")
    if any(token in blob for token in _IMPERSONATION):
        return True
    return "rugmunch" in blob and any(r in blob for r in ("support", "admin", "official"))


def _promo_hit(text: str, policy) -> str | None:
    for pattern in links.patterns(policy.promo_patterns):
        match = pattern.search(text)
        if match:
            return match.group(0)[:60]
    return None


def _hit(policy, rule: str, action: str, reason: str, matched: str) -> Action:
    return Action(
        rule=rule,
        action=action,
        reason=reason,
        matched=matched,
        reply=messages.warn(rule, reason, matched, policy.appeal_note),
        delete=action == "delete",
    )


def evaluate_text(ctx: MsgContext, policy, stats: MessageStats) -> Action | None:
    """Return the action this message earns, or None when it is clean."""
    text = ctx.text or ""
    low = text.lower()

    for url in ctx.urls:
        needle = links.scam_needle(url, policy.scam_domains)
        if needle:
            return _hit(policy, "scam_link", "delete", f"blocklisted scam domain ({needle})", url)
    for url in ctx.urls:
        if links.INVITE_RE.search(url):
            return _hit(policy, "invite_link", policy.invite_action, "chat invite link", url)
    for needle in policy.scam_substrings:
        if needle in low:
            return _hit(
                policy, "scam_phrase", policy.promo_action, f"scam phrase: {needle}", needle
            )
    promo = _promo_hit(text, policy)
    if promo:
        return _hit(policy, "promo", policy.promo_action, "promotional pitch", promo)
    for url in ctx.urls:
        if not links.link_allowed(url, policy.links_allow):
            return _hit(
                policy, "unlisted_link", policy.link_action, "link outside the allowlist", url
            )
    if ctx.media and stats.media_burst >= policy.media_burst and stats.is_new_member:
        return Action(
            rule="media_burst",
            action=policy.nsfw_action,
            reason=f"{stats.media_burst} media posts within 60s from a new account",
            reply=messages.warn(
                "media_burst", "rapid media posting from a new account", "", policy.appeal_note
            ),
            delete=policy.nsfw_action == "delete",
        )
    if stats.duplicate_count >= policy.dup_count:
        return Action(
            rule="repeat_message",
            action=policy.dup_action,
            reason=f"same message x{stats.duplicate_count} within {policy.dup_window_s}s",
            reply=messages.warn(
                "repeat_message", f"repeat x{stats.duplicate_count}", "", policy.appeal_note
            ),
            delete=policy.dup_action == "delete",
        )
    if stats.user_velocity >= policy.user_msgs:
        return Action(
            rule="user_velocity",
            action=policy.user_velocity_action,
            reason=f"{stats.user_velocity} msgs within {policy.velocity_window_s}s",
            mute_minutes=max(1, policy.velocity_window_s),
        )
    if stats.chat_velocity >= policy.chat_msgs:
        return Action(
            rule="chat_raid",
            action=policy.chat_velocity_action,
            reason=f"{stats.chat_velocity} msgs in chat within {policy.velocity_window_s}s",
            reply=messages.raid_alert(
                ctx.chat_title, stats.chat_velocity, policy.velocity_window_s
            ),
        )
    if _is_impostor(ctx):
        return _hit(
            policy,
            "impersonation",
            policy.promo_action,
            "display name impersonates our team",
            ctx.display_name,
        )
    return None


__all__ = ["evaluate_text"]
