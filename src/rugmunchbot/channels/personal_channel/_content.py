"""Personal channel content — operator-voice captions + stricter rerank gate.

Extracted verbatim from the former single-module ``channels/personal_channel.py``.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from rugmunchbot.channels.personal_channel._config import (
    PERSONAL_CHANNEL_POST_LIMIT,
    PERSONAL_CHANNEL_THRESHOLD,
)

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import NewsArticle
    from rugmunchbot.channels.importance import ScoredArticle

# ══════════════════════════════════════════════════════════════════════════
# OPERATOR VOICE — edit here
# ══════════════════════════════════════════════════════════════════════════
# Canonical voice source: PERSONA_CONTEXT (v10) in
# app/domains/telegram/rugmunchbot/prompts.py — "sharp degen-fluent
# intelligence analyst": blunt truth-telling, data-first, no corporate-speak,
# never fabricate metrics. These templates render ONE story per post in that
# voice. Placeholders: {title} (real headline), {lead} (clipped excerpt of
# the real body), {source} (channel name), {url} (canonical t.me link).
# The operator tunes these phrasings directly.
CAPTION_TEMPLATES: tuple[str, ...] = (
    "🚨 {title}\n\n{lead}\n\nFull story: {url}\n— via {source}",
    "{title}\n\nThis one matters: {lead}\n\n{url} · via {source}",
    "🔥 {title}\n\n{lead}\n\nRead the full story: {url} (source: {source})",
    "{title}\n\n{lead} — {url}\n\nvia {source}",
)

_LEAD_CHARS = 240


def _lead(article: NewsArticle) -> str:
    """First sentence(s) of the real body, clipped. Never invents content."""
    text = (article.body or article.title or "").strip()
    if not text:
        return ""
    if len(text) <= _LEAD_CHARS:
        return text
    cut = text[:_LEAD_CHARS]
    # Prefer breaking at a sentence boundary when one exists in the clip.
    for boundary in (". ", "! ", "? ", ".\n"):
        idx = cut.rfind(boundary)
        if idx > 40:  # keep at least a meaningful first sentence
            return cut[: idx + 1].strip()
    return cut.rstrip() + "…"


def build_caption(article: NewsArticle, template_idx: int | None = None) -> str:
    """One operator-voice caption for a story.

    ``template_idx`` is deterministic (hash of the canonical source URL) when
    omitted, so the same story renders the same way on re-post. The caption is
    built exclusively from the real article — title, clipped body lead,
    source channel, canonical URL. No facts are fabricated.
    """
    if template_idx is None:
        digest = hashlib.sha256(article.source_url.encode("utf-8", errors="ignore")).hexdigest()
        template_idx = int(digest[:8], 16) % len(CAPTION_TEMPLATES)
    template = CAPTION_TEMPLATES[template_idx % len(CAPTION_TEMPLATES)]
    return template.format(
        title=article.title.strip(),
        lead=_lead(article),
        source=article.source_channel or "Telegram",
        url=article.source_url.strip(),
    )


# ── Selection (stricter reranker gate) ──────────────────────────────────────


def select_personal(scored: list[ScoredArticle], top_n: int | None = None) -> list[ScoredArticle]:
    """Return the stories worth the personal channel, best first.

    Rerank mode: keep ``importance >= PERSONAL_CHANNEL_THRESHOLD`` (default
    0.7 — the "bigger stories" above the news channel's ``IMPORTANCE_THRESHOLD``
    of 0.4), sorted by importance desc. Fallback mode (reranker down): keep
    the top-N by heuristic score so the channel still flows during an outage.
    Never invents content — it only rearranges what the sweep normalized.
    """
    limit = max(1, top_n or PERSONAL_CHANNEL_POST_LIMIT)
    if not scored:
        return []
    if scored[0].fallback:
        ordered = sorted(scored, key=lambda s: s.importance, reverse=True)
    else:
        ordered = sorted(
            (s for s in scored if s.importance >= PERSONAL_CHANNEL_THRESHOLD),
            key=lambda s: s.importance,
            reverse=True,
        )
    return ordered[:limit]
