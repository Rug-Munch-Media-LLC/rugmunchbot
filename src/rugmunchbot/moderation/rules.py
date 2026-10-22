"""Text/media rules — the first matching rule wins, each names itself for audit.

Pure functions over (MsgContext, ChatPolicy, MessageStats): no I/O, no
Telegram, no clock beyond the stats the caller already computed. Order is the
gate order: hard scam signals before soft promo, cheap string checks before
window counters, identity last (a clean message from an impostor still fires).
"""

from __future__ import annotations

from rugmunchbot.moderation import links, messages, names, nsfw
from rugmunchbot.moderation.models import Action, MsgContext
from rugmunchbot.moderation.state import MessageStats  # noqa: TC001 — annotation only


def _promo_hit(text: str, policy) -> str | None:
    for pattern in links.patterns(policy.promo_patterns):
        match = pattern.search(text)
        if match:
            return match.group(0)[:60]
    return None


def _hit(rule: str, action: str, reason: str, matched: str, policy, *, delete=None) -> Action:
    cut = action == "delete" if delete is None else delete
    return Action(
        rule=rule,
        action=action,
        reason=reason,
        matched=matched,
        reply=messages.warn(rule, reason, matched, policy.appeal_note),
        delete=cut,
    )


def evaluate_text(ctx: MsgContext, policy, stats: MessageStats) -> Action | None:
    """Return the action this message earns, or None when it is clean."""
    text = ctx.text or ""
    low = text.lower()

    for url in ctx.urls:
        needle = links.scam_needle(url, policy.scam_domains)
        if needle:
            return _hit("scam_link", "delete", f"blocklisted scam domain ({needle})", url, policy)
    for url in ctx.urls:
        if links.INVITE_RE.search(url):
            return _hit("invite_link", policy.invite_action, "chat invite link", url, policy)
    nsfw_reason = nsfw.hit(ctx.media_id, policy)
    if nsfw_reason:
        return _hit("nsfw_media", policy.nsfw_action, nsfw_reason, ctx.media_id[:40], policy)
    for needle in policy.scam_substrings:
        if needle in low:
            return _hit(
                "scam_phrase", policy.promo_action, f"scam phrase: {needle}", needle, policy
            )
    promo = _promo_hit(text, policy)
    if promo:
        return _hit("promo", policy.promo_action, "promotional pitch", promo, policy)
    for url in ctx.urls:
        if not links.link_allowed(url, policy.links_allow):
            return _hit(
                "unlisted_link", policy.link_action, "link outside the allowlist", url, policy
            )
    if ctx.media and stats.media_burst >= policy.media_burst and stats.is_new_member:
        return _hit(
            "media_burst",
            policy.nsfw_action,
            f"{stats.media_burst} media posts within 60s from a new account",
            ctx.media,
            policy,
        )
    if stats.duplicate_count >= policy.dup_count:
        return _hit(
            "repeat_message",
            policy.dup_action,
            f"same message x{stats.duplicate_count} within {policy.dup_window_s}s",
            text[:60],
            policy,
        )
    if stats.user_velocity >= policy.user_msgs:
        return Action(
            rule="user_velocity",
            action=policy.user_velocity_action,
            reason=f"{stats.user_velocity} msgs within {policy.velocity_window_s}s",
            mute_minutes=policy.mute_minutes,
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
    impostor = names.impersonation_reason(ctx, policy)
    if impostor:
        return _hit(
            "impersonation", policy.impersonation_action, impostor, ctx.display_name, policy
        )
    return None


__all__ = ["evaluate_text"]
