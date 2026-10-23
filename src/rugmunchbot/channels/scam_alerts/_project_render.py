"""Telegram HTML card for a project-level scam alert.

Renders exactly the fields the operator asked for: why it is a scam, the
evidence with sources, who is pushing it, source links, timestamps,
confidence and the disclaimer. Escape-first: every external string is
HTML-escaped before it reaches Telegram.
"""

from __future__ import annotations

import html
from typing import Any

MAX_CHARS = 3800
_BANNER = {"critical": ("🚨", "CRITICAL SCAM ALERT"), "high": ("⚠️", "SCAM ALERT")}
_PUSHER_LABEL = {
    "channel": "Channel",
    "wallet": "Wallet",
    "deployer": "Deployer",
    "account": "Account",
}


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _title(project: dict[str, Any]) -> str:
    name = _esc(project.get("name"))
    symbol = _esc(project.get("symbol"))
    if name and symbol and name.lower() != symbol.lower():
        return f"{name} (${symbol})"
    return (symbol and f"${symbol}") or name or "Unnamed project"


def _why_lines(why: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(why, start=1):
        lines.append(
            f"{index}. <b>{_esc(item.get('label'))}</b> — {_esc(item.get('detail'))}\n"
            f"   <i>source: {_esc(item.get('source'))}</i>"
        )
    return lines


def _pusher_lines(pushers: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for pusher in pushers:
        label = _PUSHER_LABEL.get(str(pusher.get("kind")), "Actor")
        role = f" — {_esc(pusher.get('role'))}" if pusher.get("role") else ""
        lines.append(
            f"• <b>{label}</b> <code>{_esc(pusher.get('handle'))}</code>{role}\n"
            f"   <i>source: {_esc(pusher.get('source'))}</i>"
        )
    return lines


def render_project_alert(alert: dict[str, Any]) -> str:
    """Render alert dict → Telegram HTML (never exceeds the caption limit)."""
    project = alert.get("project") or {}
    emoji, banner = _BANNER.get(str(alert.get("severity")), ("🔎", "SCAM WATCH"))
    confidence = alert.get("confidence") or {}
    lines = [f"{emoji} <b>{banner}</b>", "", f"🎯 <b>{_title(project)}</b>"]
    chain = _esc(project.get("chain"))
    if chain:
        lines.append(f"<i>chain: {chain}</i>")
    for entry in project.get("addresses") or []:
        lines.append(f"<code>{_esc(entry.get('address'))}</code>")

    lines += ["", "🧨 <b>Why this is a scam</b>"]
    lines += _why_lines(alert.get("why") or [])

    pushers = alert.get("pushers") or []
    lines += ["", "📣 <b>Who is pushing it</b>"]
    lines += _pusher_lines(pushers) if pushers else ["• <i>no attributable pusher found yet</i>"]

    lines += [
        "",
        f"📊 <b>Confidence:</b> {_esc(confidence.get('label'))} "
        f"({_esc(confidence.get('score'))}) — <i>{_esc(confidence.get('basis'))}</i>",
    ]
    if alert.get("first_seen"):
        lines.append(f"🕐 <b>First seen:</b> {_esc(alert.get('first_seen'))}")
    lines.append(f"🕐 <b>Checked:</b> {_esc(alert.get('timestamp'))}")

    sources = [s for s in alert.get("sources") or [] if s]
    if sources:
        lines += ["", "🔗 <b>Sources</b>"]
        lines += [f"• {_esc(source)}" for source in sources[:6]]

    address = (
        (project.get("addresses") or [{}])[0].get("address") if project.get("addresses") else ""
    )
    if address:
        lines.append(f"🤖 <b>Scan it yourself:</b> /scan <code>{_esc(address)}</code>")
    lines += ["", f"⚖️ <i>{_esc(alert.get('disclaimer'))}</i>"]

    post = "\n".join(lines)
    return post if len(post) <= MAX_CHARS else post[: MAX_CHARS - 1] + "…"
