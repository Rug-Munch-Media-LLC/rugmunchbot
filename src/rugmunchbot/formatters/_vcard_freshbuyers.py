"""First-buyer forensics for the verdict-first scan card.

Additive data contract (backend)::

    first_buyers[]{address, first_buy_ts, bought_sol, current_pct, sold_pct,
                   realized_sol, wallet_age_days, funder_type, cluster_id}
    fresh_wallets{count, share_of_first_buyers, under_1d, under_7d,
                  funded_by_deployer, funded_by_same_funder, p50_age_days}
    scam_signals[]{id, severity, text, metric}

EVERY field is optional. A block nobody filled renders NOTHING at all — no
line, no em dash, no "unknown" — so an older payload leaves the card exactly
as it was. ``first_buyers`` is accepted as this list OR as the legacy
``{"buyers": [...], "limit": n}`` dict (same engine, older serializer).

The block is capped at ``MAX_LINES`` content lines (fresh line, top-3
suspicious wallets, one signals line) and the card drops it first when
Telegram's 4096-character cap bites.

One public entry point: :func:`first_buyer_lines`.
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.formatters._vcard_fields import num, short_addr

FRESH = "\U0001f9ea"  # 🧪
BULLET = "  \u2022 "
SEV_EMOJI = {
    "critical": "\U0001f6a8",
    "high": "\u26a0\ufe0f",
    "medium": "\U0001f538",
    "low": "\U0001f539",
}
SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
#: Content lines (separator excluded) — never push the card over 4096.
MAX_LINES = 5
#: Funder words that make a fresh wallet structurally suspicious.
HOT_FUNDERS = {
    "deployer": 6.0,
    "dev": 6.0,
    "team": 6.0,
    "insider": 5.0,
    "mixer": 5.0,
    "tornado": 5.0,
    "bridge": 4.0,
}
SUSPECT_LIMIT = 3


def buyers_of(r: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise ``first_buyers`` (list or ``{"buyers": [...]}`` dict)."""
    raw = r.get("first_buyers")
    if isinstance(raw, dict):
        raw = raw.get("buyers")
    if not isinstance(raw, list):
        return []
    return [b for b in raw if isinstance(b, dict) and (b.get("address") or b.get("wallet"))]


def addr_of(buyer: dict[str, Any]) -> str:
    """Buyer address whichever key the serializer used."""
    return str(buyer.get("address") or buyer.get("wallet") or "")


def fresh_line(r: dict[str, Any]) -> str:
    """``🧪 Fresh buyers: 12/20 under 7d · 6 funded by deployer · p50 age 0.3d``."""
    block = r.get("fresh_wallets")
    if not isinstance(block, dict) or not block:
        return ""
    count = num(block.get("count"))
    share = num(block.get("share_of_first_buyers"))
    tracked = len(buyers_of(r))
    total = tracked if tracked and (count is None or tracked >= count) else None
    if total is None and count and share and 0 < share <= 1:
        total = round(count / share)
    bits: list[str] = []
    under7 = num(block.get("under_7d"))
    under1 = num(block.get("under_1d"))
    if under7 is not None or under1 is not None:
        n = int(under7 if under7 is not None else under1)
        label = "under 7d" if under7 is not None else "under 24h"
        bits.append(f"{n}/{total} {label}" if total else f"{n} {label}")
    elif count is not None:
        bits.append(f"{int(count)}/{total} fresh" if total else f"{int(count)} fresh")
    for key, template in (
        ("funded_by_deployer", "{n} funded by deployer"),
        ("funded_by_same_funder", "{n} share funder"),
    ):
        value = num(block.get(key))
        if value is not None:
            bits.append(template.format(n=int(value)))
    p50 = num(block.get("p50_age_days"))
    if p50 is not None:
        bits.append(f"p50 age {p50:.1f}d")
    return f"{FRESH} Fresh buyers: " + " \u00b7 ".join(bits) if bits else ""


def _suspicion(buyer: dict[str, Any]) -> float:
    """Heuristic rank: hot funder > shared cluster > age < 7d > bag size."""
    score = HOT_FUNDERS.get(str(buyer.get("funder_type") or "").strip().lower(), 0.0)
    if buyer.get("cluster_id") not in (None, "", 0):
        score += 3.0
    age = num(buyer.get("wallet_age_days"))
    if age is not None:
        score += 3.0 if age < 1 else 2.0 if age < 7 else 0.0
    if score <= 0:
        return 0.0  # an old wallet with a plain funder is not suspicious at all
    held = num(buyer.get("current_pct"))
    if held is not None and held > 0:
        score += min(held, 10.0) / 10.0  # bag size only sharpens a real signal
    return score


def _row(buyer: dict[str, Any], score: float) -> tuple[float, str, str]:
    """``(score, first_buy_ts, line)`` — only the fields the payload carried."""
    parts = [f"<code>{html.escape(short_addr(addr_of(buyer)))}</code>"]
    age = num(buyer.get("wallet_age_days"))
    if age is not None:
        parts.append(f"{age:.1f}d")
    funder = str(buyer.get("funder_type") or "").strip()
    if funder:
        parts.append(html.escape(funder))
    held = num(buyer.get("current_pct"))
    if held is not None:
        parts.append(f"holds {held:.1f}%")
    return score, str(buyer.get("first_buy_ts") or ""), BULLET + " \u00b7 ".join(parts)


def suspect_lines(r: dict[str, Any], limit: int = SUSPECT_LIMIT) -> list[str]:
    """Top-N suspicious fresh wallets — none when nothing is actually suspicious."""
    rows = [_row(b, _suspicion(b)) for b in buyers_of(r)]
    rows = [row for row in rows if row[0] > 0]
    rows.sort(key=lambda row: (-row[0], row[1]))
    return [line for _score, _ts, line in rows[:limit]]


def signal_line(r: dict[str, Any], limit: int = 3) -> str:
    """``Signals: 🚨 8 first buyers share funder 0xab…`` — severity order."""
    raw = r.get("scam_signals")
    if not isinstance(raw, list):
        return ""
    signals = [s for s in raw if isinstance(s, dict) and str(s.get("text") or "").strip()]
    if not signals:
        return ""
    signals.sort(key=lambda s: SEV_RANK.get(str(s.get("severity") or "").lower(), 9))
    parts: list[str] = []
    for signal in signals[:limit]:
        text = str(signal["text"]).strip()
        metric = signal.get("metric")
        shown = "" if metric is None else str(metric).strip()
        if shown and shown not in text:
            text = f"{text} ({shown})"
        emoji = SEV_EMOJI.get(str(signal.get("severity") or "").lower(), BULLET.strip())
        parts.append(f"{emoji} {html.escape(text)}")
    return "  Signals: " + " \u00b7 ".join(parts)


def first_buyer_lines(r: dict[str, Any]) -> list[str]:
    """The card's first-buyer block (``[]`` when the payload has nothing)."""
    lines = [line for line in (fresh_line(r),) if line]
    lines.extend(suspect_lines(r))
    signals = signal_line(r)
    if signals:
        lines.append(signals)
    lines = lines[:MAX_LINES]
    return ["", *lines] if lines else []
