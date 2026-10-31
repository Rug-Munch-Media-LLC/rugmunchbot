"""Numeric-verification layer of the chat guardrail (numbers pass).

Moved out of ``_guards.py`` unchanged: the symbol/address guardrail cannot
catch a hallucinated NUMBER ("$420.69", "+999%" carry no ticker to strip),
so every ``$amount`` / ``±%`` claim is cross-checked against the real numbers
the live tools returned, and flagged inline when nothing corroborates it.
"""

from __future__ import annotations

import bisect
import math
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rugmunchbot.tools.registry import ToolResult

__all__ = ["_collect_numeric_facts", "_verify_numeric_claims"]

# ══════════════════════════════════════════════
# NUMERIC-VERIFICATION (zero-fabrication, numbers layer)
# ══════════════════════════════════════════════
# The symbol/address guardrail (tools/guardrails.py::sanitize_answer) has no
# way to catch a hallucinated NUMBER — "$420.69" or "+999%" carry no ticker
# to strip. These helpers add the numbers layer: every ``$amount`` / ``±%``
# claim in the answer is cross-checked (cheap regex + tolerant membership)
# against the real numbers the live tools returned (market_overview /
# price_lookup / scanner / rag_lookup payloads). A claim matching no fetched
# datum is flagged inline — visibly, never silently stripped.

# USD amounts: "$420.69", "$12,345", "$2.5M", "$50 million". A "$" followed
# by a DIGIT — a "$" followed by LETTERS is a ticker (TOKEN_RE in
# tools/guardrails.py) and is deliberately NOT matched here.
_USD_CLAIM_RE = re.compile(
    r"\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*(K|M|B|million|billion|trillion))?\b",
    re.IGNORECASE,
)
# Signed percentage moves: "+2.3%", "-12.5%". Unsigned percentages in prose
# ("42% of supply") are not market claims of the +X%/-X% shape this pass
# targets, so they are deliberately left alone.
_PERCENT_CLAIM_RE = re.compile(r"([+-]\d+(?:\.\d+)?)\s*%")
# Human-readable unit suffixes on $amount claims.
_CLAIM_SUFFIX_MULT = {
    "k": 1e3,
    "m": 1e6,
    "b": 1e9,
    "million": 1e6,
    "billion": 1e9,
    "trillion": 1e12,
}
# Tolerance rules: a claim is verified when some fetched datum lies within
# REL_TOL relative (2%) — or ABS_FLOOR absolute (sub-cent prices) — of it.
REL_TOL = 0.02
ABS_FLOOR = 1e-9


def _claim_value(raw: str, suffix: str | None) -> float | None:
    """Parse a captured claim's number (+ optional K/M/B/million suffix)."""
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    if suffix:
        value *= _CLAIM_SUFFIX_MULT.get(suffix.lower(), 1.0)
    return value


def _walk_numeric(node: Any, out: list[float]) -> None:
    """Collect every finite numeric leaf of a tool payload (dict/list/str)."""
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        if math.isfinite(node):
            out.append(float(node))
        return
    if isinstance(node, str):
        # Numeric strings ("0.00001234") are real data (DexScreener etc.).
        try:
            value = float(node.replace(",", ""))
        except ValueError:
            return
        if math.isfinite(value):
            out.append(value)
        return
    if isinstance(node, dict):
        for child in node.values():
            _walk_numeric(child, out)
    elif isinstance(node, (list, tuple)):
        for child in node:
            _walk_numeric(child, out)


def _collect_numeric_facts(live_results: dict[str, ToolResult]) -> list[float]:
    """Every real number the tools returned — the datum set to verify against.

    Skips failed tools and web_search knowledge fallbacks (synthesized text,
    not corroborating live data — mirrors the ``tools_ok`` gate in
    ``_post_guardrail``).
    """
    out: list[float] = []
    for result in live_results.values():
        if not result.ok or (
            result.tool == "web_search" and str(result.source).startswith("knowledge")
        ):
            continue
        if result.data is not None:
            _walk_numeric(result.data, out)
    return out


def _within_tolerance(claim: float, known: list[float]) -> bool:
    """Tolerant set-membership: is ``claim`` within tolerance of a datum?

    ``known`` must be a sorted, deduplicated list of fetched numbers.
    Bisection checks only the two nearest neighbors — O(log n) per claim,
    no API calls, fully deterministic.
    """
    index = bisect.bisect_left(known, claim)
    for datum in (
        known[index - 1] if index > 0 else None,
        known[index] if index < len(known) else None,
    ):
        if datum is not None and abs(claim - datum) <= max(REL_TOL * abs(datum), ABS_FLOOR):
            return True
    return False


def _verify_numeric_claims(answer: str, known_nums: list[float]) -> tuple[str, list[str]]:
    """Flag inline every $amount / ±% claim that matches no fetched datum.

    Verified claims (a fetched number within tolerance) are left untouched;
    unmatched claims get ``" ⚠ unverified"`` appended after the number.
    Returns ``(flagged_answer, flagged_claims)``. With an empty datum set the
    answer is returned unchanged — no data means nothing to verify against
    (the NO_LIVE_DATA path handles that).
    """
    known = sorted({k for k in known_nums if math.isfinite(k)})
    if not known:
        return answer, []
    flagged: list[str] = []

    def _flag(match: re.Match[str], value: float | None) -> str:
        if value is None:
            return match.group(0)
        # Idempotency guard: never double-flag an already-flagged claim.
        if answer[match.end() :].lstrip().startswith("⚠ unverified"):
            return match.group(0)
        if _within_tolerance(value, known):
            return match.group(0)
        flagged.append(match.group(0))
        return f"{match.group(0)} ⚠ unverified"

    answer = _USD_CLAIM_RE.sub(lambda m: _flag(m, _claim_value(m.group(1), m.group(2))), answer)
    answer = _PERCENT_CLAIM_RE.sub(lambda m: _flag(m, _claim_value(m.group(1), None)), answer)

    # Readability doctrine: >3 inline flags means the datum set is partial and
    # the prose drowns in warnings. Collapse to one honest footnote instead.
    inline = answer.count(" ⚠ unverified")
    if inline > 3:
        answer = answer.replace(" ⚠ unverified", "")
        flagged = list(dict.fromkeys(flagged))
        footer = (
            "\n\n⚠ Some figures above could not be verified against live "
            f"sources ({len(flagged)} unchecked). Treat them as indicative."
        )
        if "⚠" not in answer:
            answer += footer
    return answer, flagged
