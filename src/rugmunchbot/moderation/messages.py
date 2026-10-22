"""User-facing moderation texts. HTML only, every dynamic value escaped."""

from __future__ import annotations

from html import escape


def _quote(text: str, limit: int = 160) -> str:
    return escape((text or "")[:limit], quote=False)


def warn(rule: str, reason: str, snippet: str, appeal_note: str) -> str:
    """Generic in-chat warning card posted as a reply to the offender."""
    return (
        f"🛡️ <b>Moderation</b> — {escape(rule)}\n"
        f"Reason: {escape(reason)}\n"
        f"Message: <i>{_quote(snippet)}</i>\n"
        f"{escape(appeal_note)}"
    )


def address_warning(address: str, chain: str, verdict: str, reason: str, note: str) -> str:
    """Warning card for a posted contract address that scanned badly."""
    icon = "⛔" if verdict == "avoid" else "⚠️"
    return (
        f"{icon} <b>Address warning</b> ({escape(chain)})\n"
        f"<code>{escape(address)}</code>\n"
        f"Verdict: <b>{escape(verdict)}</b> — {escape(reason)}\n"
        f"{escape(note)}"
    )


def captcha_prompt(minutes: int) -> str:
    return (
        "👋 <b>Welcome!</b>\n"
        f"Tap the button within {minutes} min to prove you are human and unlock the chat.\n"
        "Accounts that never tap are removed."
    )


def captcha_math(question: str, minutes: int) -> str:
    return (
        "👋 <b>Welcome!</b>\n"
        f"Solve to prove you are human: <b>{escape(question)}</b>\n"
        f"You have {minutes} min. A wrong answer removes you."
    )


def captcha_failed(kicked: bool) -> str:
    return "🚪 <b>Gate closed</b> — captcha not solved.\n" + (
        "You were removed; you may rejoin and try again." if kicked else "You stay muted for now."
    )


def welcome(name: str) -> str:
    who = f"<b>{escape(name)}</b>" if name else "friend"
    return (
        f"✅ {who}, you're in.\n"
        "Read the pinned rules, never share a seed phrase, and DM us nothing but /appeal."
    )


def raid_alert(chat_title: str, count: int, window_s: int) -> str:
    return (
        f"🚨 <b>Raid suspected</b> in {escape(chat_title)}\n"
        f"{count} messages in {window_s}s across the chat.\n"
        "No accounts were actioned — admins, please review and use /modactions."
    )


def appeal_receipt(action_id: str) -> str:
    return (
        "📬 Appeal logged against action "
        f"<code>{escape(action_id)}</code>. An admin will review it. "
        "Abusing appeals gets you muted."
    )


__all__ = [
    "address_warning",
    "appeal_receipt",
    "captcha_failed",
    "captcha_math",
    "captcha_prompt",
    "raid_alert",
    "warn",
    "welcome",
]
