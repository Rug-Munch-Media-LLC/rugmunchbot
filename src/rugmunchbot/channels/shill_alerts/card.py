"""Compact evidence card + moderation action buttons.

The card is deliberately short: entity, chain, confidence, the matched
signals, the source links, on-chain evidence, and the word "alleged". It is
an allegation with evidence attached, never a verdict against a named party.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rugmunchbot.channels.shill_alerts.models import Alert

#: Callback namespace. Registered in ``handlers/callback/_actions.py``.
CALLBACK_PREFIX = "shill:"

_ACTIONS: tuple[tuple[str, str], ...] = (
    ("QUARANTINE_CHANNEL", "Quarantine channel"),
    ("BAN_ACTOR", "Ban actor"),
    ("MARK_FALSE_POSITIVE", "False positive"),
    ("ESCALATE", "Escalate"),
)


def _esc(value: object) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _short(entity: str) -> str:
    return f"{entity[:8]}…{entity[-6:]}" if len(entity) > 18 else entity


def render_alert(alert: Alert) -> tuple[str, list[list[tuple[str, str]]]]:
    """Return ``(html_text, buttons)`` for one alert.

    ``buttons`` is the raw shape the caller hands to
    ``InlineKeyboardMarkup``: rows of ``(label, callback_data)``.
    """
    chain = alert.chain or "unknown"
    conf = alert.verdict.confidence
    names = "; ".join(f"{s.name} ({s.detail})" for s in alert.signals[:5])

    lines = [
        f"⚠️ <b>Alleged coordinated promo</b> — <code>{_esc(_short(alert.entity))}</code>",
        f"<b>Chain:</b> {_esc(chain)} · <b>Confidence:</b> {conf:.2f} "
        f"({len(alert.signals)} signals / {len(alert.verdict.families)} families)",
    ]

    risk = alert.risk or {}
    level = str(risk.get("risk_level") or "").upper()
    if level or risk.get("note"):
        detail = risk.get("note") or f"scanner verdict {level}"
        lines.append(f"<b>Risk:</b> {_esc(level or 'flagged')} — {_esc(str(detail)[:120])}")

    lines.append(f"<b>Signals:</b> {_esc(names)}")

    sources = alert.sources[:3]
    if sources:
        linked = " ".join(f'<a href="{_esc(url)}">[{i + 1}]</a>' for i, url in enumerate(sources))
        extra = len(alert.sources) - len(sources)
        lines.append(
            f"<b>Sources ({len(alert.sources)}):</b> {linked}"
            + (f" +{extra} more" if extra > 0 else "")
        )
    if alert.channels:
        lines.append(f"<b>Channels:</b> {_esc(', '.join(alert.channels[:6]))}")

    lines.append("")
    lines.append("<i>Allegation, not a verdict. Evidence links above; verify before engaging.</i>")

    buttons = [
        [
            (
                label,
                f"{CALLBACK_PREFIX}{code.lower()}:{alert.key}",
            )
            for code, label in _ACTIONS[:2]
        ],
        [
            (
                label,
                f"{CALLBACK_PREFIX}{code.lower()}:{alert.key}",
            )
            for code, label in _ACTIONS[2:]
        ],
    ]
    return "\n".join(lines), buttons


def render_review_line(alert: Alert) -> str:
    """One-line entry for the review queue (no buttons, no alarm styling)."""
    return (
        f"review · {alert.entity[:20]} · {alert.chain or 'unknown'} · "
        f"conf {alert.verdict.confidence:.2f} · {len(alert.verdict.families)} family · "
        f"{alert.verdict.reasons[0] if alert.verdict.reasons else 'insufficient'}"
    )


def inline_markup(buttons: list[list[tuple[str, str]]]) -> Any:
    """Build a Telegram ``InlineKeyboardMarkup``; ``None`` when telegram is absent."""
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    except Exception:  # pragma: no cover - telegram is a hard dep in prod
        return None
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label, callback_data=data) for label, data in row]
            for row in buttons
        ]
    )
