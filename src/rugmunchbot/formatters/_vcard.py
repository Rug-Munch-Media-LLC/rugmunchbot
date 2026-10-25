"""Verdict-first scan card with per-number provenance (one message, one truth).

Layout, top to bottom (and why):

  1. ``VERDICT WORD \u00b7 score/100 \u00b7 $SYMBOL`` — worst-of(security, bundle,
     dev), never an average (``_vcard_verdict``);
  2. identity — name (symbol) \u00b7 chain + the full address;
  3. ONE safety row — mint/freeze authority, LP lock/burn, tax, honeypot;
     any hard fail already forced AVOID;
  3b. forensic block (optional, dropped FIRST) — fresh first buyers, the
     top-3 suspicious wallets, scam signals, then CEX-funded fresh wallets,
     top funders and holder mix (``_vcard_freshbuyers``/``_vcard_funding``);
     an absent payload renders nothing at all;
  4. clusters — per cohort ``bought -> current -> realised SOL`` with counts;
  5. dev — rug-rate + funder, never a launch count;
  6. KOL — matched / registry size (coverage), not "not tracked";
  7. wash — labelled recycling when it is detected;
  8. market — compact, LAST (price/mcap/liq/vol24);
  9. the why sentence, then ``Updated HH:MMZ``.

Every number carries ``\u00b7 source \u00b7 HH:MMZ``; a value the scan could not
establish renders ``\u2014`` with the reason (never ``0``). Top-10 is omitted
rather than printed as ``0.00%``. Optional blocks (KOL, wash, holders,
deployer, footer) are dropped first to stay inside Telegram's 4096-char cap;
the verdict, safety, clusters and market blocks are never dropped.

One public entry point: :func:`format_verdict_card`.
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.config import fmt_chain
from rugmunchbot.formatters._base import footer_links, sep
from rugmunchbot.formatters._vcard_evidence import normalize
from rugmunchbot.formatters._vcard_fields import field_line, source_for, stamp
from rugmunchbot.formatters._vcard_fit import fit_card
from rugmunchbot.formatters._vcard_freshbuyers import first_buyer_lines
from rugmunchbot.formatters._vcard_funding import funding_lines
from rugmunchbot.formatters._vcard_intel import intel_lines
from rugmunchbot.formatters._vcard_signals import collect
from rugmunchbot.formatters._vcard_verdict import WORD_EMOJI, verdict

TELEGRAM_LIMIT = 4096
TRUNCATED = "\u2026 (card truncated)"

__all__ = ["TELEGRAM_LIMIT", "format_verdict_card"]


def _short(address: str) -> str:
    addr = str(address or "")
    return f"{addr[:4]}\u2026{addr[-4:]}" if len(addr) > 10 else addr or "?"


def _head(r: dict[str, Any], word: str) -> str:
    """Line 1: verdict word, score, symbol — the only thing that must be read."""
    raw = r.get("risk_score")
    try:
        score = None if raw in (None, "") else float(raw)
    except (TypeError, ValueError):
        score = None
    symbol = str(r.get("symbol") or "")
    if symbol in ("", "???", "Unknown"):
        symbol = _short(str(r.get("address", "")))
    ticker = html.escape(symbol if symbol.startswith("$") else f"${symbol}")
    # An unpublished score is rendered as an em dash, never as 0: a withheld
    # score means "not established", and "0/100" reads as "verified clean".
    shown = "\u2014" if score is None else f"{score:.0f}"
    return f"{WORD_EMOJI.get(word, chr(0x2753))} <b>{word} \u00b7 {shown}/100 \u00b7 {ticker}</b>"


def _optional(r: dict[str, Any], ts: str) -> list[list[str]]:
    """Late, droppable blocks in pruning order (last is dropped first)."""
    blocks: list[list[str]] = []
    holders = r.get("top_holders")
    if isinstance(holders, list) and holders:
        rows = ["", f"\U0001f465 <b>Top holders</b> \u00b7 onchain \u00b7 {ts}"]
        for i, h in enumerate(holders[:3], 1):
            if not isinstance(h, dict):
                continue
            pct = h.get("pct")
            pct_str = f"{float(pct):.1f}%" if isinstance(pct, (int, float)) else "\u2014"
            tag = f" [{h['tag']}]" if h.get("tag") else ""
            rows.append(
                f"  {i}. <code>{html.escape(str(h.get('address', '')))}</code> {pct_str}{tag}"
            )
        if len(rows) > 1:
            blocks.append(rows)
    deployer = r.get("deployer_address") or r.get("dev_wallet")
    if deployer:
        blocks.append(
            [
                "",
                f"\U0001f468\u200d\U0001f4bb <b>Deployer</b> \u00b7 engine \u00b7 {ts}",
                f"  <code>{html.escape(str(deployer))}</code>",
            ]
        )
    links = footer_links()
    if links:
        blocks.append(["", links])
    return blocks


def _forensic_variants(r: dict[str, Any]) -> list[list[str]]:
    """Forensic groups, richest first — dropped before any core row.

    Order matters: the CEX / holder-quality lines die first, then the
    first-buyer forensics, then (in :func:`fit_card`) the legacy tail blocks.
    A payload with neither block leaves the card exactly as it was.
    """
    buyers = first_buyer_lines(r)
    funding = funding_lines(r)
    if not buyers and not funding:
        return [[]]
    return [[*buyers, *funding], buyers, []]


def format_verdict_card(
    r: dict[str, Any],
    *,
    updated_at: Any = None,
    max_chars: int = TELEGRAM_LIMIT,
) -> str:
    """Render the card, pruning optional blocks first to fit ``max_chars``."""
    ts = stamp(updated_at)
    r = normalize(r)  # fill flat safety keys from evidence.security, never invent
    signals = collect(r, ts)
    word, why = verdict(r, signals, ts)

    base = [
        _head(r, word),
        f"{html.escape(str(r.get('name') or 'Unknown'))} ({html.escape(str(r.get('symbol') or '?'))})"
        f" \u00b7 {fmt_chain(r.get('chain', 'unknown'))}",
        f"<code>{html.escape(str(r.get('address', '')))}</code>",
        sep(),
        signals.safety,
    ]
    tail: list[str] = []
    if signals.top10:  # rule 2: a real measurement, or no row at all
        tail.append(field_line("Top-10", "top10", signals.top10, source_for(r, "top10"), ts))
    tail.extend(intel_lines(r, ts, word=word))
    tail.extend(signals.clusters)
    tail.extend([signals.dev, signals.kol])
    if signals.wash:
        tail.append(signals.wash)
    tail.extend([sep(), signals.market])
    if why:
        tail.extend(["", why])
    tail.extend(["", f"Updated {ts}"])

    return fit_card(
        base,
        tail,
        _forensic_variants(r),
        _optional(r, ts),
        max_chars=max_chars,
        truncated=TRUNCATED,
    )
