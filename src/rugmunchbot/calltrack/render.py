"""Deterministic text renderers for the call-tracking surface (no I/O)."""

from __future__ import annotations

from html import escape
from typing import Any

_CALLS_SHOWN = 10


def short_addr(address: str) -> str:
    """Truncate an address the way every card in the bot does."""
    if len(address) <= 12:
        return address
    return f"{address[:6]}…{address[-4:]}"


def _caller(row: dict[str, Any]) -> str:
    name = row.get("username")
    return f"@{escape(name)}" if name else f"user {row.get('user_id')}"


def render_call_card(row: dict[str, Any], stored: bool) -> str:
    """One card for `/call` — states only what was stored, never a price."""
    token = row.get("token_address", "")
    chain = row.get("chain") or "chain unresolved"
    head = "📌 <b>Call logged</b>" if stored else "📌 <b>Already called</b>"
    lines = [
        head,
        "",
        f"Token: <code>{escape(token)}</code>",
        f"Chain: {escape(chain)}",
        f"First caller: {_caller(row)}",
    ]
    if row.get("note"):
        lines.append(f"Note: {escape(str(row['note']))}")
    if not stored:
        lines.append("\n<i>First caller wins — this token is already on the board.</i>")
    lines.append("\n<b>/lb</b> board · <b>/calls</b> recent · <b>/fc</b> who was first")
    return "\n".join(lines)


def render_calls(rows: list[dict[str, Any]], limit: int = _CALLS_SHOWN) -> str:
    """The group's most recent calls, newest first."""
    if not rows:
        return "🗒 <b>No calls yet</b>\n\nLog one with <code>/call &lt;contract&gt;</code>."
    lines = ["🗒 <b>Recent calls</b>", ""]
    for row in rows[:limit]:
        note = f" — {escape(str(row['note']))}" if row.get("note") else ""
        lines.append(f"• {short_addr(str(row['token_address']))} · {_caller(row)}{note}")
    return "\n".join(lines)


def render_leaderboard(rows: list[dict[str, Any]], label: str) -> str:
    """Rank callers by count. Explicitly refuses to imply a PnL number."""
    if not rows:
        return f"🏆 <b>Leaderboard · {escape(label)}</b>\n\nNo calls logged in this window yet."
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 <b>Leaderboard · {escape(label)}</b>", ""]
    for idx, row in enumerate(rows):
        mark = medals[idx] if idx < len(medals) else f"{idx + 1}."
        lines.append(f"{mark} {_caller(row)} — {row['calls']} call(s)")
    lines.append(
        "\n<i>Counts of logged calls only. Returns are not claimed here — "
        "check a token with /scan before you act.</i>"
    )
    return "\n".join(lines)
