"""Verdict decision for the scan card — worst-of, never an average.

A card that averages a clean security score with a rug-rate deployer and a
bundled launch reads "MEDIUM" on a token nobody should buy (measured on the
same mints against `@PirbViewBot` / DevsNightmare / TrenchScanner). The
verdict here is the WORST of three independent dimensions:

  * security — any hard fail (honeypot, live mint, live freeze, sell tax
    >= 50%) is ``AVOID`` outright, whatever else says; otherwise the
    scanner's own risk word;
  * bundle   — ``bundle_analysis.is_bundled`` true is HIGH;
  * dev      — ``deployer_profile.rug_rate`` >= 0.5 is HIGH.

Contradiction guard (rule 10): a HIGH score with no safety answer and no
cohort/dev evidence is reported as ``INSUFFICIENT EVIDENCE`` — the score is
not supportable, so it is not presented as a confident verdict.

One public entry point: :func:`verdict`.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._scan_report_common import _risk_level_word

RANK_WORD = {3: "AVOID", 2: "HIGH RISK", 1: "MEDIUM RISK", 0: "LOW RISK"}
UNSUPPORTED = "INSUFFICIENT EVIDENCE"
#: Verdict word -> leading emoji (one glyph, standard Unicode).
WORD_EMOJI = {
    "AVOID": "\U0001f6d1",
    "HIGH RISK": "\U0001f6a8",
    "MEDIUM RISK": "\u26a0\ufe0f",
    "LOW RISK": "\u2705",
    UNSUPPORTED: "\u2753",
}

__all__ = ["UNSUPPORTED", "WORD_EMOJI", "verdict"]


#: The scanner publishes this vocabulary (``scoring.verdict.BANDS``); the card
#: must read it rather than re-derive a budget from the raw number, otherwise a
#: capped heuristic and a hard fail both arrive as "whatever 75 means here".
DERIVED_RANK = {"safe": 0, "caution": 1, "elevated": 2, "critical": 3}


def _score_rank(r: dict[str, Any]) -> int | None:
    """Risk score -> rank (HIGH 2 / MEDIUM 1 / LOW 0); ``None`` when unpublished."""
    raw = r.get("risk_score")
    if raw in (None, ""):
        return None
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    return {"HIGH": 2, "MEDIUM": 1, "LOW": 0}.get(_risk_level_word(score), 1)


def _derived_rank(r: dict[str, Any]) -> int | None:
    """The scanner's own band, when it published one."""
    return DERIVED_RANK.get(str(r.get("verdict") or "").lower())


def _dev_rank(r: dict[str, Any]) -> int | None:
    """Deployer rug-rate -> rank (>=50% HIGH, >=20% MEDIUM)."""
    profile = r.get("deployer_profile") if isinstance(r.get("deployer_profile"), dict) else {}
    evidence = (
        (r.get("evidence") or {}).get("deployer") if isinstance(r.get("evidence"), dict) else {}
    )
    raw = profile.get("rug_rate", evidence.get("rug_rate") if isinstance(evidence, dict) else None)
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        return None
    return 2 if rate >= 0.5 else 1 if rate >= 0.2 else 0


def _hard_fail_reasons(r: dict[str, Any]) -> list[str]:
    """Human words for each hard fail (rule 5), empty when none."""
    out: list[str] = []
    if r.get("honeypot") is True:
        out.append("honeypot")
    if r.get("mintable") is True:
        out.append("mint authority live")
    if r.get("freezable") is True:
        out.append("freeze authority live")
    try:
        if float(r.get("sell_tax", 0) or 0) >= 0.5:
            out.append("sell tax >= 50%")
    except (TypeError, ValueError):
        pass
    return out


def verdict(r: dict[str, Any], signals: Any, ts: str) -> tuple[str, str]:
    """Return ``(head_word, why_line)`` for the card's first two blocks."""
    try:
        score = float(r.get("risk_score", 0) or 0)
    except (TypeError, ValueError):
        score = 0.0
    fails = _hard_fail_reasons(r)
    if signals.hard_fail and not fails:
        fails = ["safety hard fail"]
    if fails:
        return RANK_WORD[3], "Why AVOID: " + ", ".join(fails)

    if str(r.get("verdict") or "").lower() == "inconclusive":
        reason = str(r.get("inconclusive_reason") or "coverage was incomplete")
        return UNSUPPORTED, f"Why unknown: {reason}."

    bundle = r.get("bundle_analysis") if isinstance(r.get("bundle_analysis"), dict) else {}
    ranks = [rank for rank in (_derived_rank(r), _score_rank(r), _dev_rank(r)) if rank is not None]
    if bundle.get("is_bundled") is True:
        ranks.append(2)
    rank = max(ranks) if ranks else 1

    unsupported = rank >= 2 and not signals.has_evidence
    if unsupported:
        return UNSUPPORTED, f"No safety or cluster evidence for a {score:.0f}/100 score."
    from rugmunchbot.formatters._scan_report_main import _evidence_verdict_line

    return RANK_WORD[rank], _evidence_verdict_line(r) or ""
