"""Report guardrails — fact-recency gate + link validation (2026-10-17).

Shared by the three daily reports (Morning Brief, Midday Market Report,
Evening Wrap). Two deterministic, fail-open checks run BEFORE the quality
gate so an undated old claim or a dead link never ships:

* :func:`recency_gate` — rejects a report whose dated claims are all older
  than the window (the exact vector by which months-old stories slip in:
  vector similarity ignores publish date). A report with NO dated claims
  passes (the LLM is instructed to cite-or-omit; absence is not a lie).
* :func:`validate_links` — HEAD-checks every http(s) link in the report and
  returns the dead ones. Fail-open: a network error is NOT a dead link.

Both are pure-ish (no side effects beyond an optional HTTP probe) and never
raise.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("rmi_bot.channels.report_guardrails")

#: Matches an ISO date (YYYY-MM-DD) or a "Month DD, YYYY" date in prose.
_ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_PROSE_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{1,2}),\s+(20\d{2})\b"
)
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_LINK_RE = re.compile(r"https?://[^\s)>\"']+")


def _extract_dates(text: str) -> list[datetime]:
    """Every parseable date in *text* (ISO + prose), newest-agnostic."""
    out: list[datetime] = []
    for y, m, d in _ISO_DATE_RE.findall(text):
        try:
            out.append(datetime(int(y), int(m), int(d), tzinfo=UTC))
        except ValueError:
            continue
    for month, day, year in _PROSE_DATE_RE.findall(text):
        try:
            out.append(datetime(int(year), _MONTHS[month.lower()], int(day), tzinfo=UTC))
        except (ValueError, KeyError):
            continue
    return out


def recency_gate(text: str, *, max_age_hours: float = 48.0) -> tuple[bool, str]:
    """True when the report's dated claims are within *max_age_hours*.

    Returns ``(ok, reason)``. A report with NO dated claims passes — the
    prompt enforces cite-or-omit, so absence of a date is not a stale claim.
    When dated claims exist, the NEWEST must be inside the window; a report
    whose freshest claim is older than the window is rejected as stale.
    """
    dates = _extract_dates(str(text or ""))
    if not dates:
        return True, "no_dated_claims"
    newest = max(dates)
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    if newest < cutoff:
        age_h = (datetime.now(UTC) - newest).total_seconds() / 3600.0
        return False, f"stale_newest_claim age_h={age_h:.0f} max={max_age_hours:.0f}"
    return True, "fresh"


def extract_links(text: str) -> list[str]:
    """Distinct http(s) links in *text*, order-preserving."""
    return list(dict.fromkeys(_LINK_RE.findall(str(text or ""))))


async def validate_links(text: str, *, timeout_s: float = 6.0) -> list[str]:
    """Return the DEAD links in *text* (fail-open: network error ≠ dead).

    A link is dead only on a definitive 4xx/5xx response. Timeouts, DNS
    failures and connection errors are treated as alive so a flaky network
    never blocks a report.
    """
    links = extract_links(text)
    if not links:
        return []
    try:
        import httpx
    except Exception:  # httpx missing — cannot validate, fail open
        return []
    dead: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            for url in links:
                try:
                    resp = await client.head(url)
                    if resp.status_code >= 400:
                        # Some servers reject HEAD — retry with GET before judging.
                        resp = await client.get(url)
                    if resp.status_code >= 400:
                        dead.append(url)
                except Exception:  # noqa: S112 — network error is NOT a dead link (fail-open)
                    continue
    except Exception as exc:  # fail-open
        logger.debug("report_guardrails:link_validation_failed err=%s", str(exc)[:120])
        return []
    if dead:
        logger.info("report_guardrails:dead_links count=%d", len(dead))
    return dead


async def report_guardrails(
    text: str,
    *,
    max_age_hours: float = 48.0,
    check_links: bool = True,
) -> tuple[bool, list[str]]:
    """Run both gates. Returns ``(ok, reasons)`` — never raises.

    ``ok=False`` means the caller must NOT ship (log ``reasons`` and skip or
    regenerate). Link validation is skipped when ``check_links`` is False.
    """
    reasons: list[str] = []
    ok, reason = recency_gate(text, max_age_hours=max_age_hours)
    if not ok:
        reasons.append(reason)
    if check_links:
        dead = await validate_links(text)
        if dead:
            reasons.append(f"dead_links={len(dead)}")
    return (not reasons), reasons


__all__ = [
    "extract_links",
    "recency_gate",
    "report_guardrails",
    "validate_links",
]
