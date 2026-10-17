"""Normalisation + presentation helpers for ranked news items.

Ranked items cross two boundaries before they reach a prompt: the upstream
rugbrain row shape and the ``cite-or-omit`` prompt block format. Both live
here so the fetch lane stays a single transport function and neither boundary
is reimplemented per post type.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

#: Ranked stories rendered per post (top-K of the ranker).
TOP_K = 8


def parse_published_at(raw: Any) -> datetime | None:
    """Parse an ISO-8601 string, an epoch seconds/ms number, or ``None``.

    Epoch values above 1e12 are treated as milliseconds. A naive datetime is
    assumed UTC. Returns ``None`` for anything unparseable — callers treat
    that as "undated", never as an error.
    """
    if not raw:
        return None
    try:
        if isinstance(raw, (int, float)):
            ts = float(raw)
            if ts > 1e12:  # ms epoch
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=UTC)
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError, OSError):
        return None


def article_score(article: dict[str, Any]) -> float:
    """Importance score for a ``/news/top`` row (int-or-dict ``quality``).

    ``quality`` is an int on heuristic-tier rows and a dict on others, so it
    is probed rather than assumed: a bare ``.get("quality", {}).get(...)``
    raises AttributeError on the int rows, which the caller's fail-open
    except then swallowed into an empty list.
    """
    raw = article.get("score")
    try:
        if raw is not None:
            return float(raw)
    except (TypeError, ValueError):
        pass
    q = article.get("quality")
    if isinstance(q, dict):
        try:
            return float(q.get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(q or 0.0)
    except (TypeError, ValueError):
        return 0.0


def normalise_article(a: dict[str, Any]) -> dict[str, Any]:
    """Map an upstream ``/news/top`` row to the internal ranked-item shape."""
    return {
        "title": a.get("headline") or a.get("title") or "",
        "published_at": a.get("published_at") or "",
        "url": a.get("url") or "",
        "image_url": a.get("image_url") or "",
        "score": article_score(a),
        "source": a.get("source") or "",
        "_ranked_by": a.get("ranked_by") or "",
    }


def _field(it: dict[str, Any], key: str, limit: int | None = None) -> str:
    """Trimmed string field (optionally truncated)."""
    raw = str(it.get(key) or "").strip()
    return raw[:limit] if limit is not None else raw


def _item_line(it: dict[str, Any]) -> str:
    """One cite-or-omit line: title/summary, category, source, url."""
    title = _field(it, "title")
    summary = _field(it, "summary", 220)
    bits = [title or summary[:80]]
    for label in ("category", "source"):
        value = _field(it, label)
        if value:
            bits.append(f"{label} {value}")
    if summary and title:
        bits.append(summary)
    line = " — ".join(b for b in bits if b)
    url = _field(it, "url")
    return f"{line} [url={url}]" if url.startswith(("http://", "https://")) else line


def to_prompt_block(items: list[dict[str, Any]], *, label: str) -> str:
    """Serialize ranked items into the cite-or-omit prompt block format."""
    lines = [ln for ln in (_item_line(it) for it in items[:TOP_K]) if ln.strip()]
    if not lines:
        return ""
    return f"{label}: " + " || ".join(lines)
