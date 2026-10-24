"""Holder and bundle summary rows for the verdict-first scan card.

Competitor cards lead with holder size and a bundle verdict (PIRBView's
WhaleMap, Trench's bundle score, GMGN's DEX audit). Ours printed concentration
and the launch cohorts, but never a holder count and never the bundle answer --
it only influenced the verdict implicitly. Two rows close that gap without
duplicating the cohort block:

* ``Holders`` -- total count plus fresh-wallet share, read from whichever
  engine answered (``_holders_count`` / ``_fresh_wallet_pct``);
* ``Bundle``  -- the engine's own verdict: wallet count and confidence when a
  bundle was detected, ``none detected`` when the launch window scanned clean,
  and the engine's reason when it has no answer at all.

Rule: nothing is derived here. A number the scan did not report renders ``-``
plus the reason, never ``0``; a missing bundle payload never reads as "clean".
The rows are appended to the card's droppable tail, so a long card loses them
before it loses the verdict or the market block.

One public entry point: :func:`intel_lines`.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._scan_report_common._fields import (
    _bundle_wallet_count,
    _float_or_none,
    _fresh_wallet_pct,
    _holders_count,
    _lp_lock_pct,
)
from rugmunchbot.formatters._vcard_fields import DASH, source_for

__all__ = ["intel_lines"]

SELL_TAX_HARD_FAIL = 0.5


def _row(label: str, value: str, source: str, ts: str) -> str:
    """One padded provenance row (same geometry as ``field_line``)."""
    return f"  {label:<12} {value} \u00b7 {source} \u00b7 {ts}"


def _holders_row(r: dict[str, Any], ts: str) -> str:
    """Holder count + fresh-wallet share, or ``-`` with the engine's reason."""
    bits: list[str] = []
    count = _holders_count(r)
    if count is not None:
        bits.append(f"<b>{count:,}</b>")
    fresh = _fresh_wallet_pct(r)
    if fresh is not None:
        bits.append(f"fresh {fresh:.0f}%")
    if not bits:
        return f"  {'Holders':<12} {DASH} \u00b7 {source_for(r, 'holders')}: no holder data"
    return _row("Holders", " \u00b7 ".join(bits), source_for(r, "holders"), ts)


def _bundle_bits(block: dict[str, Any]) -> list[str]:
    """Detail bits for a detected bundle: wallets, confidence, risk label."""
    bits: list[str] = []
    wallets = _bundle_wallet_count(block)
    if wallets is not None:
        bits.append(f"\U0001f534 <b>{wallets:,}</b> wallets")
    conf = _float_or_none(block.get("confidence"))
    if conf is not None:
        bits.append(f"{conf:.0f}% conf")
    label = str(block.get("risk_label") or "").strip().upper()
    if label:
        bits.append(label)
    return bits


def _bundle_detected_row(r: dict[str, Any], block: dict[str, Any], ts: str) -> str:
    """Row for a detected bundle -- always shows at least the red dot."""
    bits = _bundle_bits(block) or ["\U0001f534 detected"]
    return _row("Bundle", " \u00b7 ".join(bits), source_for(r, "bundle"), ts)


def _bundle_unscanned_row(block: dict[str, Any]) -> str:
    """Row when the engine answered without a verdict: its reason, capped."""
    reason = str(block.get("reason") or block.get("result") or "not scanned").strip()
    return f"  {'Bundle':<12} {DASH} \u00b7 engine: {reason[:60]}"


def _bundle_row(r: dict[str, Any], ts: str) -> str:
    """Bundle verdict -- detected, clean-scanned, or the engine's own reason."""
    block = r.get("bundle_analysis")
    if not isinstance(block, dict):
        return f"  {'Bundle':<12} {DASH} \u00b7 engine: bundle engine not run"
    if block.get("is_bundled") is True:
        return _bundle_detected_row(r, block, ts)
    if str(block.get("result") or "").lower() == "scanned":
        return _row("Bundle", "\U0001f7e2 none detected", source_for(r, "bundle"), ts)
    return _bundle_unscanned_row(block)


def _contract_reasons(r: dict[str, Any]) -> list[str]:
    """Hard fails the contract itself reports: honeypot, authorities, tax."""
    out: list[str] = []
    if r.get("honeypot") is True:
        out.append("honeypot \u2014 sells can be blocked")
    if r.get("mintable") is True:
        out.append("mint authority live")
    if r.get("freezable") is True:
        out.append("freeze authority live")
    tax = _float_or_none(r.get("sell_tax"))
    if tax is not None and tax >= SELL_TAX_HARD_FAIL:
        out.append("sell tax \u2265 50%")
    return out


def _deployer_reasons(r: dict[str, Any]) -> list[str]:
    """Repeat-rug deployer: only when the engine measured a 50%+ rug rate."""
    profile = r.get("deployer_profile")
    rate = _float_or_none(profile.get("rug_rate")) if isinstance(profile, dict) else None
    if rate is None or rate < 0.5:
        return []
    return [f"deployer rugged {rate * 100:.0f}% of past launches"]


def _concentration_reasons(r: dict[str, Any]) -> list[str]:
    """Top-10 concentration, when the scan actually measured it."""
    concentration = r.get("holder_concentration")
    measured = concentration.get("top10_pct") if isinstance(concentration, dict) else None
    top10 = _float_or_none(measured)
    if top10 is None or top10 < 50:
        return []
    return [f"top-10 hold {top10:.0f}%"]


def _liquidity_reasons(r: dict[str, Any]) -> list[str]:
    """Thin LP lock, when a lock percentage is known."""
    lock = _lp_lock_pct(r)
    if lock is None or lock >= 20:
        return []
    return [f"LP only {lock:.0f}% locked"]


def _reasons(r: dict[str, Any]) -> list[str]:
    """Up to three ranked reasons, each a fact the scan actually returned."""
    ranked = [
        *_contract_reasons(r),
        *_deployer_reasons(r),
        *_concentration_reasons(r),
        *_liquidity_reasons(r),
    ]
    return ranked[:3]


def intel_lines(r: dict[str, Any], ts: str, *, word: str = "") -> list[str]:
    """Holder and bundle rows (+ up to three reasons unless AVOID lists them).

    Args:
        r: Scan report payload.
        ts: Observation stamp (``HH:MMZ``).
        word: Verdict word; ``AVOID`` already enumerates its hard fails in the
            card's why line, so the reasons row is suppressed.

    Returns:
        Card lines to append to the droppable tail.
    """
    lines = [_holders_row(r, ts), _bundle_row(r, ts)]
    reasons = _reasons(r)
    if reasons and word != "AVOID":
        lines.append(f"  {'Reasons':<12} " + " \u00b7 ".join(reasons))
    return lines
