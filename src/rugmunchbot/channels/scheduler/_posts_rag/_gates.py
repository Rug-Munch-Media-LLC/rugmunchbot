"""RAG-drafts — quality / recency / source / news-content-type gates.

Pure gate functions. References the package namespace (``_pkg``) for the
regex constants and config so monkeypatching the package propagates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import rugmunchbot.channels.scheduler._posts_rag as _pkg

logger = _pkg.logger


def _draft_age_hours(item: dict[str, Any]) -> tuple[float | None, str]:
    """Best-effort age of a draft in hours + which probe matched.

    Order: explicit ``ts``/``published_at`` field → first date embedded in
    the text. Returns ``(None, "none")`` when no date could be found.
    """
    raw = str(item.get("ts") or item.get("published_at") or "").strip()
    if raw:
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            return max((datetime.now(UTC) - ts).total_seconds() / 3600.0, 0.0), "field"
        except ValueError as e:
            logger.debug("draft_age:unparsable_ts raw=%r err=%s", raw, e)
    m = _pkg._EMBEDDED_DATE_RE.search(str(item.get("text") or ""))
    if m:
        try:
            if m.group(1):  # ISO form 2024-09-16
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:  # "September 16, 2024"
                y = int(m.group(6))
                mo = _pkg._MONTHS.get((m.group(4) or "").lower(), 0)
                d = int(m.group(5))
            if mo and d:
                ts = datetime(y, mo, d, tzinfo=UTC)
                return max((datetime.now(UTC) - ts).total_seconds() / 3600.0, 0.0), "text"
        except (ValueError, TypeError):
            return None, "none"
    return None, "none"


def _recency_gate(item: dict[str, Any]) -> str:
    """Stale-news gate. Returns "" when the draft may post, else the reason.

    A draft fails when: it carries no discoverable date at all (undated =
    unverifiable freshness), or its age exceeds RAG_POST_MAX_AGE_H. Pure —
    never raises; unparsable dates count as undated.
    """
    if _pkg.RAG_POST_MAX_AGE_H <= 0:
        return ""
    age_h, probe = _draft_age_hours(item)
    if age_h is None:
        return "undated"
    if age_h > _pkg.RAG_POST_MAX_AGE_H:
        return f"stale({age_h:.0f}h via {probe})"
    return ""


def _source_gate(item: dict[str, Any]) -> str:
    """No-source gate. Returns "" when the draft may post, else the reason.

    A draft fails when its text contains no http(s) link at all — an
    unsourced intelligence post is unverifiable and must not publish
    (silence beats slop). Pure — never raises.
    """
    if _pkg._SOURCE_LINK_RE.search(str(item.get("text") or "")):
        return ""
    return "no_source_link"


def _news_content_type_gate(item: dict[str, Any]) -> str:
    """News-channel content-type gate. Returns "" when the draft may post to
    @rmicryptonews, else the reason.

    Blocks any draft whose ``kind`` is a non-news content type (whale alerts,
    scan results, system notifications, product updates, shill/alpha intel,
    roundup/digest summaries). This is the channel-doctrine filter that keeps
    @rmicryptonews breaking-crypto-news-only. Pure — never raises; unknown or
    missing kinds pass (the other gates still apply).
    """
    kind = str(item.get("kind") or "").strip().lower()
    if not kind:
        return ""
    if kind in _pkg._NEWS_BLOCKED_KINDS:
        return f"blocked_kind({kind})"
    return ""


def _quality_gate(item: dict[str, Any]) -> str:
    """WO-3 draft gate. Returns "" when the draft may post, else the reason.

    Checks (in order): explicit ``writer == "template"`` marker (rugbrain
    2026-08-25), the template signature heuristic (covers containers built
    before the marker shipped), and the rerank-score floor. Pure — never
    raises; unparsable scores pass.

    2026-08-30 unify (task 2): the importance floor now reads ``importance``
    too, not just ``score``/``rerank_score``. Rugbrain pre-scores its drafts
    into ``score``/``rerank_score``; the bot's own reranker
    (``quality_schedule.rerank_quality`` — used on the fallback lane) writes
    ``importance``. Checking all three means the 0.30 floor applies uniformly
    — a reranked fallback headline scoring 0.01-0.12 can no longer slip
    through just because it had no ``score`` field (the old unscored bypass).
    """
    writer = str(item.get("writer") or "").strip().lower()
    if writer == "template":
        return "template_writer"
    text = str(item.get("text") or "")
    # Template-fallback output has many shapes: "[SEV] Title\n\nSource:" AND
    # the "Story — Source:" headline-placeholder variant. Both are the LLM
    # writer being down — neither ever posts (silence beats slop). The
    # placeholder ref-URL gate is applied regardless of the ``writer`` label —
    # a template that slipped through unlabeled still must not publish.
    if _pkg._TEMPLATE_SIGNATURE_RE.match(text.strip()) or _pkg._STORY_TEMPLATE_RE.match(
        text.strip()
    ):
        return "template_signature"
    if _pkg._PLACEHOLDER_SOURCE_RE.search(text):
        return "placeholder_source"
    # Confirmed-score keys: rugbrain ("score"/"rerank_score") + bot reranker
    # ("importance"). Any explicit score under the floor is DISCARDED; a
    # missing/unparsable score means "no signal" and passes (the rugbrain
    # importance metadata is sparse today) — but once WE rerank a fallback
    # draft it carries a real ``importance``, so it never slips unscored.
    for key in ("score", "rerank_score", "importance"):
        raw_score = item.get(key)
        if raw_score is None:
            continue
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            return ""  # unparsable → no usable signal → pass
        if score > 0 and score < _pkg.RMI_RAG_POST_MIN_SCORE:
            return f"below_threshold({score:.2f})"
    return ""
