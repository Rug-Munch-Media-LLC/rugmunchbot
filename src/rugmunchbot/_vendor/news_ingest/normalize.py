# VENDORED 2026-08-17 from rmi-backend/app/domains/news_ingest/normalize.py (Phase 2a — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Normalization: raw Telegram channel messages → ``NewsArticle``.

Pure helpers only — no network, no DB, no Telegram dependency. Regexes for
EVM / Solana addresses mirror the canonical patterns in ``app.rag.dedup`` so
entity extraction stays consistent with the RAG entity-overlap dedup level.
"""

from __future__ import annotations

import re
from typing import Final

from rugmunchbot._vendor.news_ingest.models import ChannelMessage, NewsArticle
from rugmunchbot._vendor.dedup import content_hash

# ── Tunable thresholds ─────────────────────────────────────────────────────

_MIN_NEWS_CHARS: Final = 60  # shorter (and entity-free) → promo/telegraph noise
#: Terse whale/alpha signals ("Whale moved 5,000 BTC") are high-value and
#: typically entity-free + short — give the alpha category a lower floor so
#: routing to the alpha channel is not starved by the news noise gate.
_MIN_ALPHA_CHARS: Final = 20
_FORWARD_ONLY_MAX_CHARS: Final = 120  # short forwarded posts are re-share noise
_TITLE_MAX_CHARS: Final = 120

# ── Bot junk / promo line patterns (stripped before classification) ────────

_JUNK_LINE_RES: Final = (
    re.compile(
        r"(?i)^\s*(join|subscribe|follow|add|share)\s+(our|the|this|my)?\s*(channel|group|telegram|chat|page)\b"
    ),
    re.compile(r"(?i)^\s*(join|follow|subscribe|add)\s+(us|now|me)\b"),
    re.compile(r"(?i)^\s*(buy|sell|pump|dump|moon|gem|signal|alpha|call)\b.{0,40}\b(now|soon|fast|🚀|🔥|💎|🤑)\b"),
    re.compile(r"(?i)^\s*(dm|message)\s+(me|us|for)\b"),
    re.compile(r"(?i)^\s*(watch|read|see|listen)\s+(the\s+)?(full|more|video|thread)\b"),
    re.compile(r"(?i)^\s*(click|tap)\s+(here|the link)\b"),
    re.compile(r"(?i)^\s*powered by rugmunch\b"),
    re.compile(r"(?i)^\s*(🚀|🔥|💎|💰|🤑|🎁|✅)\s*$"),
    re.compile(r"^\s*t\.me/\S+\s*$"),  # bare join-link footer lines
    re.compile(r"^\s*https?://\S+\s*$"),  # bare URL lines (mirrors rag.dedup)
)

# ── Entity regexes (same shapes as app.rag.dedup) ──────────────────────────

_EVM_ADDRESS_RE: Final = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_SOLANA_ADDRESS_RE: Final = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_SYMBOL_RE: Final = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,11})\b")

# ── Source URL resolution ────────────────────────────────────────────────────

#: Any http(s) URL in a message text (loose; trailing punctuation is stripped).
_URL_RE: Final = re.compile(r"https?://[^\s<>]+")
#: Telegram channel/message links (t.me / telegram.me) — never a real source.
_TME_LINK_RE: Final = re.compile(r"^https?://(?:t\.me|telegram\.me)/", re.IGNORECASE)

# ── English-only language gate (Unicode script ranges, never translation) ───

#: Characters from non-English scripts that mark a message as non-English:
#: Greek, Cyrillic, Hebrew, Arabic, Devanagari, Thai, Hiragana/Katakana, CJK
#: ideographs, and Hangul (incl. jamo). Accented Latin (é/ü/ñ) is deliberately
#: NOT flagged — script detection cannot tell "café" from a French article,
#: and the rule is English-only content, not Latin-script-only.
_NON_ENGLISH_SCRIPT_RE: Final = re.compile(
    "["
    "\u0370-\u03ff"  # Greek + Coptic
    "\u0400-\u052f"  # Cyrillic + Cyrillic Supplement
    "\u0590-\u05ff"  # Hebrew
    "\u0600-\u06ff"  # Arabic
    "\u0750-\u077f"  # Arabic Supplement
    "\u0900-\u097f"  # Devanagari
    "\u0e00-\u0e7f"  # Thai
    "\u1100-\u11ff"  # Hangul Jamo
    "\u3040-\u30ff"  # Hiragana + Katakana
    "\u3130-\u318f"  # Hangul Compatibility Jamo
    "\u31f0-\u31ff"  # Katakana Phonetic Extensions
    "\u3400-\u4dbf"  # CJK Unified Ideographs Extension A
    "\u4e00-\u9fff"  # CJK Unified Ideographs
    "\uac00-\ud7af"  # Hangul Syllables
    "]"
)


def extract_entities(text: str) -> list[str]:
    """Extract ordered, de-duplicated crypto entities from *text*.

    Recognizes ``$SYMBOL`` mentions (normalized to uppercase), full EVM
    ``0x`` 40-hex addresses, and Solana base58 addresses. Pure and
    dependency-free — no network, no KB lookups.
    """
    if not text:
        return []
    seen: set[str] = set()
    entities: list[str] = []
    for match in _SYMBOL_RE.finditer(text):
        entity = f"${match.group(1).upper()}"
        if entity not in seen:
            seen.add(entity)
            entities.append(entity)
    for match in _EVM_ADDRESS_RE.finditer(text):
        entity = match.group(0)
        if entity not in seen:
            seen.add(entity)
            entities.append(entity)
    for match in _SOLANA_ADDRESS_RE.finditer(text):
        entity = match.group(0)
        if entity not in seen:
            seen.add(entity)
            entities.append(entity)
    return entities


def strip_junk_lines(text: str) -> str:
    """Remove bot-junk / promo / bare-link lines, preserving real content."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.search(stripped) for pattern in _JUNK_LINE_RES):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def _is_noise(text: str, *, forwarded: bool, category: str = "", has_official_link: bool = False) -> bool:
    """True when *text* is promo/telegraph noise rather than a news item.

    A message that embeds the real article URL (non-t.me — *has_official_link*)
    is a news item even when terse: the headline + official link carries the
    story (PANews-style posts are exactly headline + original URL). Promo /
    junk / forward-only gates still apply.
    """
    if not text or not text.strip():
        return True
    if re.fullmatch(r"\s*(?:t\.me/|https?://)\S+\s*", text):
        return True
    if not has_official_link:
        min_chars = _MIN_ALPHA_CHARS if category == "alpha" else _MIN_NEWS_CHARS
        if len(text) < min_chars and not extract_entities(text):
            return True
    return forwarded and len(text) < _FORWARD_ONLY_MAX_CHARS


def is_english_text(text: str) -> bool:
    """True when *text* carries no non-English script characters.

    The English-only gate for the news channel: any Hangul / CJK / Japanese
    kana / Cyrillic / Arabic / Greek / Hebrew / Thai / Devanagari character
    marks the text as non-English. Pure + deterministic (Unicode ranges only —
    no language model, no translation). URLs and entities are ASCII and never
    trip the gate.
    """
    return not _NON_ENGLISH_SCRIPT_RE.search(text or "")


def is_telegram_link(url: str) -> bool:
    """True when *url* points at a Telegram page (t.me / telegram.me).

    Used to tell the channel's own links apart from the real article URL when
    resolving ``NewsArticle.source_url``.
    """
    return bool(_TME_LINK_RE.match(url or ""))


def _entity_url(entity: object) -> str | None:
    """Extract a URL from a duck-typed Telegram link entity, if any."""
    url = getattr(entity, "url", None)
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    return None


def _extract_source_url(msg: ChannelMessage) -> str:
    """Resolve the real source URL for *msg* (t.me link is the last resort).

    Priority:
      1. Inline link entities / embedded preview when the message carries
         them (duck-typed — ``ChannelMessage`` does not carry them today, but
         a richer reader may attach ``url_entities`` / ``web_preview``).
      2. The first http(s) URL in the message text that is not the channel's
         own ``t.me`` link — PANews-style posts embed the original article
         URL (e.g. ``panewslab.com`` / ``theblockbeats.info``).
      3. The canonical ``https://t.me/<channel>/<message_id>`` fallback.
    """
    for attr in ("url_entities", "entities"):
        for entity in getattr(msg, attr, None) or []:
            url = _entity_url(entity)
            if url and not is_telegram_link(url):
                return url
    preview = getattr(msg, "web_preview", None)
    if preview is not None:
        for attr in ("url", "display_url"):
            url = getattr(preview, attr, None)
            if isinstance(url, str) and url.startswith(("http://", "https://")) and not is_telegram_link(url):
                return url
    for match in _URL_RE.finditer(msg.text or ""):
        url = match.group(0).rstrip(".,;:!?)]}\"'")
        if url and not is_telegram_link(url):
            return url
    return f"https://t.me/{msg.channel_username}/{msg.message_id}"


def _message_media_reference(msg: ChannelMessage) -> str | None:
    """Best-effort media reference for photo/video messages (pure).

    Reads duck-typed ``media_id`` / ``photo_id`` / ``media_ref`` /
    ``media_url`` attributes when a richer ``ChannelMessage`` provides them.
    The Telethon media object (with the real file id) is currently dropped in
    ``ingest._message_to_channel_message`` — out of scope here — so without a
    duck-typed id the photo/video post is marked ``tg:<media_type>`` and the
    sweep's og:image fallback replaces the marker with a real image URL when
    the source page exposes one.
    """
    if msg.media_type not in ("photo", "video", "gif"):
        return None
    for attr in ("media_id", "photo_id", "media_ref", "media_url"):
        value = getattr(msg, attr, None)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return f"tg:{msg.media_type}"


def channel_message_to_article(msg: ChannelMessage, category: str) -> NewsArticle | None:
    """Convert a raw channel message into a ``NewsArticle``.

    Strips bot junk, skips forward-only noise, non-news (empty, link-only,
    short entity-free promo) messages, and **non-English messages** (the
    news channel is English-only — a Unicode-script gate, never translation).
    Returns ``None`` for anything that is not a usable news item.
    ``category=="alpha"`` relaxes the minimum length gate so terse whale/alpha
    signals survive normalization.
    """
    if msg is None or not msg.text or not msg.text.strip():
        return None

    text = strip_junk_lines(msg.text).strip()
    source_url = _extract_source_url(msg)
    has_official_link = not is_telegram_link(source_url)
    if _is_noise(
        text,
        forwarded=msg.forwarded_from is not None,
        category=category,
        has_official_link=has_official_link,
    ):
        return None
    if not is_english_text(text):
        return None  # English-only channel rule — never translate

    lines = text.splitlines()
    title = re.sub(r"\s+", " ", lines[0]).strip()[:_TITLE_MAX_CHARS]
    body = text
    entities = extract_entities(text)

    return NewsArticle(
        title=title,
        body=body,
        source_channel=msg.channel or msg.channel_username,
        source_url=source_url,
        category=category,
        posted_at=msg.posted_at,
        content_hash=content_hash(body),
        entities=entities,
        media=_message_media_reference(msg),
    )
