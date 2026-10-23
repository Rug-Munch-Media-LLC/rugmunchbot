"""Scam-alert post type — dedicated high-signal scam/rug alert card.

Operator directive (2026-10-17): a DEDICATED scam-alert post type with the
right graphics + severity framing, distinct from the generic scanner card
and the shill digest. This module owns the copy + severity framing; the
graphic comes from ``support.post_graphics.graphic_for("scam_alert")``.

Severity framing (the whole point of the type):

* ``CRITICAL`` — active drain / confirmed rug / live exploit. Red framing,
  "do not interact", the verify link front and centre.
* ``HIGH``     — strong rug signature (honeypot, LP pulled, bundled+mint).
* ``MEDIUM``   — suspicious, unconfirmed (thin LP, flagged deployer).
* ``LOW``      — watch-list only.

Every card carries: the severity banner, the token/contract, the concrete
scam intel (the actual mechanism, never a generic "high risk"), the verify
link (scanner deep link + the /scan command), and the source. Cite-or-omit:
a card with no address and no intel is not a scam alert — it is silence.
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.channels._story_cta import attach_story_cta
from rugmunchbot.channels.formatters._primitives import (
    NewsPost,
    _clip,
    _esc,
    _now_stamp,
    _risk_emoji,
    _scan_channel_shill,
    site_news_line,
)
from rugmunchbot.formatters import sep
from rugmunchbot.support.post_graphics import graphic_for

#: Severity → (banner emoji, banner label, framing line). The framing line
#: is the severity's call to action — what the reader must do, not filler.
SEVERITY_FRAMING: dict[str, tuple[str, str, str]] = {
    "critical": (
        "🔴",
        "CRITICAL SCAM ALERT",
        "Active drain / confirmed rug. Do not interact with this contract.",
    ),
    "high": (
        "🟠",
        "HIGH-RISK SCAM ALERT",
        "Strong rug signature. Treat any interaction as a loss.",
    ),
    "medium": (
        "🟡",
        "SUSPICIOUS TOKEN ALERT",
        "Unconfirmed but flagged. Verify before you touch it.",
    ),
    "low": (
        "🟢",
        "WATCH-LIST ALERT",
        "Low-confidence flag. Monitor, do not ape.",
    ),
}

#: Fallback framing when the severity is unknown (never invent a verdict).
_DEFAULT_FRAMING = (
    "⚪",
    "SCAM ALERT",
    "Flagged by RugMunch intelligence. Verify before interacting.",
)


def _severity_framing(severity: str) -> tuple[str, str, str]:
    """(emoji, label, framing) for a severity string (default when unknown)."""
    return SEVERITY_FRAMING.get(str(severity or "").strip().lower(), _DEFAULT_FRAMING)


def _scam_intel_lines(item: dict[str, Any]) -> list[str]:
    """The concrete scam intel — the actual mechanism, never generic filler.

    Prefers an explicit ``scam_intel`` / ``verdict`` / ``summary`` string;
    falls back to the scanner's risk flags rendered as mechanism phrases.
    Returns [] when nothing concrete is known (the card then omits the
    section rather than padding it).
    """
    intel = str(item.get("scam_intel") or item.get("verdict") or item.get("summary") or "").strip()
    if intel:
        return [_esc(intel[:400])]
    flags = [str(f).strip() for f in (item.get("risk_flags") or []) if str(f).strip()]
    if flags:
        return [" · ".join(_esc(f) for f in flags[:5])]
    return []


def format_scam_alert(item: dict[str, Any]) -> NewsPost:
    """Dedicated scam-alert card: severity banner + intel + verify link.

    Distinct from ``format_scan_card`` (a scanner report) and
    ``format_scam_digest`` (a multi-item shill digest): this is ONE scam,
    framed by severity, with the verify link front and centre. Carries the
    scam-alert brand graphic on the returned ``NewsPost`` so the scheduler
    attaches it as a photo. Returns a card with no address AND no intel as
    a thin card — callers should gate on :func:`is_postable_scam_alert`.
    """
    severity = str(item.get("severity") or item.get("risk_level") or "medium").lower()
    emoji, label, framing = _severity_framing(severity)

    name = str(item.get("name") or "").strip()
    symbol = str(item.get("symbol") or "").strip()
    if name and symbol and symbol.lower() != name.lower():
        title = f"{_esc(name)} ({_esc(symbol)})"
    elif symbol:
        title = _esc(symbol)
    elif name:
        title = _esc(name)
    else:
        title = "Unnamed token"

    addr = str(item.get("address") or item.get("token_address") or "").strip()
    chain = _esc(item.get("chain") or "")

    lines = [
        f"{emoji} <b>{label}</b>",
        f"<i>{_esc(framing)}</i>",
        sep(),
        "",
        f"{_risk_emoji(severity)} <b>{title}</b>" + (f" · {chain}" if chain else ""),
    ]
    if addr:
        lines.append(f"<code>{_esc(addr)}</code>")

    intel = _scam_intel_lines(item)
    if intel:
        lines.append("")
        lines.append("🧨 <b>What's wrong:</b>")
        lines.extend(f"   {line}" for line in intel)

    # Verify link — the scanner deep link + the /scan command (front and centre).
    lines.append("")
    if addr:
        scan_url = f"https://rugmunch.io/scanner?token={addr}&chain={item.get('chain') or ''}"
        lines.append(
            f'🔍 <a href="{html.escape(scan_url, quote=True)}">Verify on RugMunch scanner</a>'
        )
        lines.append(f"🤖 <b>Scan it yourself:</b> /scan <code>{_esc(addr)}</code>")
    else:
        lines.append("🔍 Verify on rugmunch.io/scanner")

    source = str(item.get("source") or item.get("source_url") or "").strip()
    if source:
        if source.startswith(("http://", "https://")):
            lines.append(
                f'🔗 <i>Source: <a href="{html.escape(source, quote=True)}">report</a></i>'
            )
        else:
            lines.append(f"🔗 <i>Source: {_esc(source)}</i>")

    lines.extend(["", f"🕐 {_now_stamp()}"])
    lines.append(site_news_line())
    lines.append(sep())
    lines.append(_scan_channel_shill())

    post = NewsPost(attach_story_cta(_clip("\n".join(lines)), item, "scam"))
    post.image_url = graphic_for("scam_alert")
    return post


def is_postable_scam_alert(item: dict[str, Any]) -> bool:
    """True when *item* carries enough signal to be a real scam alert.

    A scam alert needs a token address OR concrete intel — a card with
    neither is noise, not an alert. Pure — never raises.
    """
    addr = str(item.get("address") or item.get("token_address") or "").strip()
    intel = str(item.get("scam_intel") or item.get("verdict") or item.get("summary") or "").strip()
    flags = [f for f in (item.get("risk_flags") or []) if str(f).strip()]
    return bool(addr or intel or flags)


__all__ = [
    "SEVERITY_FRAMING",
    "format_scam_alert",
    "is_postable_scam_alert",
]
