"""Shared text primitives — escaping, markup stripping, hooks, stamps."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import Any

_MAX_BODY_CHARS = 4090


_BREAKING_SUMMARY_CHARS = 600


_FEATURE_SUMMARY_CHARS = 420


_FEATURE_HOOK_CHARS = 160


_DIGEST_TAKEAWAY_CHARS = 150


_ALPHA_TAKEAWAY_CHARS = 120


_COMMENTARY_HOOK_CHARS = 180


_WHITESPACE_RE = re.compile(r"\s+")


_MARKUP_TAGS_RE = re.compile(r"<[^>]*>")


_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


_MARKDOWN_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)


_MARKDOWN_EMPH_RE = re.compile(r"(\*\*|__|~~|`{1,3}|>|#+\s?)")


class NewsPost(str):
    """A formatted post: a plain ``str`` plus an optional attached image URL.

    ``image_url`` is ``""`` when the post has no usable image. Because the
    value IS a ``str``, existing callers (``_safe_send``,
    ``post_news_update``, tests) keep working unchanged; photo-aware callers
    read ``.image_url`` and route through ``channels._media.send_news_photo``.
    """

    image_url: str = ""


#: URL fragments that identify SOCIAL PROFILE imagery (avatars / channel
#: headers), never post content. 2026-09-01: the @cryptorugmuncher X header
#: shipped as a post image because scraped items carried their source
#: profile's header as ``image_url``. Identity chrome, not content.
_BANNED_IMAGE_FRAGMENTS = (
    "twimg.com/profile_images",
    "/brand/banner.jpg",
    # 2026-09-01: brand chrome scraped from source posts (the custom
    # "Crypto Rug Muncher" banner ships embedded in mirrored posts) —
    # identity artwork, never post content.
    "/brand/",
    "banner",
    "cryptorugmunch.com",
)


def _pick_image_url(value: object) -> str:
    """Return *value* as a usable post image URL, else ``""``.

    2026-09-01 (always-real-bytes doctrine):
    * social-profile imagery (X headers/avatars) is REJECTED — identity
      chrome must never ship as post content;
    * external URLs are wrapped in the backend thumbnail proxy
      (``/api/v1/media/thumb?url=…``) so Telegram always receives real
      bytes even when the publisher hotlink-blocks its fetcher;
    * same-host (rugmunch.io) URLs pass through unwrapped.
    """
    if not isinstance(value, str) or not value.strip():
        return ""
    url = value.strip()
    if not url.startswith(("http://", "https://")):
        return ""
    lowered = url.lower()
    if any(fragment in lowered for fragment in _BANNED_IMAGE_FRAGMENTS):
        return ""
    try:
        from urllib.parse import quote, urlsplit

        if urlsplit(url).hostname in ("rugmunch.io", "www.rugmunch.io"):
            return url
        from rugmunchbot.config import BACKEND_URL

        return f"{BACKEND_URL}/api/v1/media/thumb?url={quote(url, safe='')}"
    except Exception:
        return url


def _graphic_for(post_type: str) -> str:
    """Brand graphic URL for a post type (lazy import — avoids a cycle).

    Delegates to ``support.post_graphics.graphic_for`` so every formatter
    can attach its per-type brand graphic without importing the support
    package at module load (which would cycle through ``channels``).
    Fail-open: any error returns "" so a formatter never crashes on a
    graphics lookup.
    """
    try:
        from rugmunchbot.support.post_graphics import graphic_for

        return graphic_for(post_type)
    except Exception:
        return ""


def _esc(value: object) -> str:
    """HTML-escape text for Telegram parse_mode=HTML (quotes left intact)."""
    return html.escape(str(value), quote=False)


def _is_placeholder(value: object) -> bool:
    """True when a name/symbol field carries no real identity (fail-open)."""
    return str(value or "").strip().lower() in ("", "unknown", "???", "n/a", "null", "none")


def _clip(text: str, limit: int = _MAX_BODY_CHARS) -> str:
    """Clip to ``limit`` WITHOUT cutting mid-word/mid-sentence.

    Prefers the last sentence end inside the budget; falls back to the
    last word boundary; only hard-cuts when a single word exceeds the
    whole budget. The ellipsis always lands on clean text.
    """
    if len(text) <= limit:
        return text
    budget = limit - 1  # room for the ellipsis
    window = text[:budget]
    # Best: last complete sentence inside the window.
    for delim in (". ", "! ", "? "):
        idx = window.rfind(delim)
        if idx > budget // 2:
            return f"{window[: idx + 1].rstrip()}…"
    # Fallback: last whitespace (never split a word).
    sp = window.rfind(" ")
    if sp > budget // 2:
        return f"{window[:sp].rstrip()}…"
    return f"{window.rstrip()}…"


def _strip_markup(text: object) -> str:
    """Flatten raw HTML/markdown from a source summary into plain text (fail-open).

    Unescapes HTML entities first (so entity-encoded tags are stripped too),
    then removes markdown link/image syntax, HTML tags, list bullets and
    emphasis markers, and collapses whitespace. Returns ``""`` when nothing
    usable remains — never raises, so a hostile summary can never inject raw
    HTML into a post.
    """
    value = str(text or "")
    if not value.strip():
        return ""
    value = html.unescape(value)
    value = _MARKDOWN_LINK_RE.sub(r"\1", value)
    value = _MARKUP_TAGS_RE.sub(" ", value)
    value = _MARKDOWN_BULLET_RE.sub("", value)
    value = _MARKDOWN_EMPH_RE.sub("", value)
    value = _WHITESPACE_RE.sub(" ", value)
    value = _strip_junk_prefixes(value)
    value = _collapse_repeated_sentences(value)
    return value.strip()


def _takeaway(text: object, limit: int = _DIGEST_TAKEAWAY_CHARS) -> str:
    """One-line plain-text hook from a summary (HTML-safe, clipped, fail-open)."""
    hook = _strip_markup(text)
    if not hook:
        return ""
    return _clip(hook, limit)


def _first_sentence_end(text: str) -> int:
    """Index just past the first complete sentence of *text* (``len(text)`` when
    no sentence delimiter exists). Shared by the hook + lede builders so the
    lede always resumes at a sentence boundary — never mid-sentence."""
    best = len(text)
    for delim in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        idx = text.find(delim)
        if idx != -1:
            end = idx + len(delim)
            if end < best:
                best = end
    return best


def _first_sentence(text: str) -> str:
    """Return the first sentence of *text* as a one-line hook (≤160 chars).

    The hook is the compelling one-liner that sits directly under a solo /
    hybrid lead headline (requirement: "rewritten title + a one-line hook",
    not a bare link). Feed it already-flattened plain text (``_strip_markup``);
    callers escape the result once at insertion time. Empty input → ``""``.
    Never raises.

    2026-09-01 (no-cut-offs doctrine): when the first sentence exceeds the
    hook budget it is clipped with ``_clip`` — sentence/word-boundary aware
    with a clean ellipsis — NEVER a raw ``text[:limit]`` hard cut that
    chopped the sentence mid-clause and pushed the fragment into the lede
    (the Aquifer post bug).
    """
    text = (text or "").strip()
    if not text:
        return ""
    first = text[: _first_sentence_end(text)].strip()
    if not first:
        return ""
    if len(first) <= _FEATURE_HOOK_CHARS:
        return first
    return _clip(first, _FEATURE_HOOK_CHARS)


def _lede_after_hook(summary: str, hook: str) -> str:
    """The rest of *summary* after the FIRST COMPLETE SENTENCE, clipped clean.

    2026-09-01: the old version sliced at ``len(hook)`` — when the hook was
    truncated to fit the budget, the lede resumed MID-SENTENCE and the
    orphan fragment landed after the commentary block (the Aquifer post:
    "…with the protocol offering the / 💡 Why it matters… / attacker a 20%
    bounty…"). The lede now always starts at the sentence boundary.
    """
    end = _first_sentence_end(summary)
    rest = summary[end:].lstrip()
    return _clip(rest, _FEATURE_SUMMARY_CHARS)


def _risk_emoji(risk_level: str) -> str:
    return {"critical": "🔴", "high": "🔴", "medium": "🟡", "low": "🟢", "safe": "✅"}.get(
        str(risk_level).lower(), "⚪"
    )


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%b %d, %H:%M UTC")


def _scan_channel_shill() -> str:
    """Cross-channel promo block for the scan card footer.

    Promotes the full RugMunch ecosystem so @rmiscans readers discover
    the bot itself, the web app, and our other channels. HTML-safe.
    """
    return (
        "\n\U0001f4e2 <b>RugMunch Intelligence</b>\n"
        '\U0001f50d <a href="https://t.me/RugMunchBot">@RugMunchBot</a> \u2014 Scan any token, anytime\n'
        '\U0001f310 <a href="https://rugmunch.io">rugmunch.io</a> \u2014 Full scanner, charts &amp; maps\n'
        '\U0001f4f0 <a href="https://t.me/rmicryptonews">@rmicryptonews</a> \u2014 Crypto news 24/7\n'
        '\U0001f6a8 <a href="https://t.me/rmialerts">@rmialerts</a> \u2014 Rug alerts &amp; exploits\n'
        '\U0001f40b <a href="https://t.me/rmialpha">@rmialpha</a> \u2014 Whale moves &amp; alpha\n'
        '\U0001f4e1 <a href="https://t.me/cryptorugmuncher">@cryptorugmuncher</a> \u2014 Curated daily'
    )


# ── Feed-junk cleanup (added 2026-08-24) ──────────────────────────────
# Incident: Coinjournal summaries ship "Key takeaways" bullets glued to
# body text AND duplicated wholesale, producing posts like
# "Key takeawaysBitcoin trades … Key takeawaysBitcoin trades …".
_JUNK_PREFIXES = ("key takeaways", "tl;dr", "tl dr", "summary:", "in brief")

_WHITESPACE_RE_COMPILED = None


def _strip_junk_prefixes(text: str) -> str:
    """Drop leading 'Key takeaways'-style labels glued to the first word."""
    lowered = text.lower().lstrip()
    for prefix in _JUNK_PREFIXES:
        if lowered.startswith(prefix):
            rest = text[len(prefix) :]
            # Glued case ("Key takeawaysBitcoin") → drop the label entirely.
            if rest and not rest[0].isspace():
                text = rest.lstrip()
                lowered = text.lower().lstrip()
            else:
                text = rest.lstrip(" :–-")  # noqa: RUF001 — strip en-dash bullets
                break
    return text


def _collapse_repeated_sentences(text: str, min_len: int = 40) -> str:
    """Collapse repeated sentences (feeds duplicate blocks wholesale) and
    strip junk labels ("Key takeaways") wherever they appear — not just at
    position 0. Short repeats like "No." are legitimate and kept.
    """
    import re as _re

    parts = _re.split(r"(?<=[.!?])\s+", text)
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        cleaned = _strip_junk_prefixes(part.strip())
        key = cleaned.lower()
        if len(key) >= min_len and key in seen:
            continue
        if len(key) >= min_len:
            seen.add(key)
        out.append(cleaned)
    return " ".join(out)


# ── W19 (2026-08-25): main-channel LINK CONTRACT ─────────────────────────
# Every @cryptorugmuncher post MUST carry at least one link. A main post
# without a source is exactly the "random bullshit with no links" the
# operator banned. Enforcement: if the formatted text has no href/http,
# append the item's scanner/dexscreener fallback; if we cannot build one,
# the caller must NOT post to main.


def has_link(text: str) -> bool:
    """True when the text carries at least one link (href or bare URL)."""
    return "href=" in text or "http://" in text or "https://" in text


def dexscreener_fallback(item: dict[str, Any]) -> str:
    """Best-effort link for an item: dexscreener when we have an address."""
    addr = str(item.get("address") or item.get("token_address") or "").strip()
    chain = str(item.get("chain") or "").strip() or "ethereum"
    if addr:
        return f"https://dexscreener.com/{chain}/{addr}"
    url = str(item.get("url") or item.get("source_url") or "").strip()
    return url


def enforce_main_link_contract(text: str, item: dict[str, Any]) -> str | None:
    """Return main-channel text guaranteed to carry a link, or None to block.

    Appends a dexscreener/source fallback line when the formatted card lacks
    any link. Returns None only when no link can be constructed at all —
    the caller must skip the main post (and log) rather than ship a
    linkless card.
    """
    if has_link(text):
        return text
    fallback = dexscreener_fallback(item)
    if not fallback:
        return None
    label = str(item.get("name") or item.get("symbol") or "source").strip()
    return f'{text}\n\n🔗 <a href="{html.escape(fallback, quote=True)}">{_esc(label)}</a>'


# ── Site-link contract (2026-08-29 content-flow overhaul) ────────────────
# Every main/channel post carries an EXACT link to the site version
# (rugmunch.io/news for news-family posts, feed.rugmunch.io for mirrored
# main posts) so Telegram drives site usage. Feed deep links use the
# in-app Ghost post route (``/blog/{slug}`` — BlogPostPage in the unified
# shell; 2026-09-01 content architecture).

#: Site news page — the canonical "see it on the site" destination.
SITE_NEWS_URL: str = "https://rugmunch.io/news"
#: Content surfaces live on the MAIN domain (2026-09-01 canonical
#: architecture — Stripe/Linear pattern): paths on rugmunch.io for content
#: sections of the same app; subdomains only for genuinely separate apps
#: (git., api., mcp., x402.). feed.rugmunch.io 301s to rugmunch.io/feed.
SITE_FEED_URL: str = "https://rugmunch.io/feed"
#: Per-post deep-link pattern — the in-app Ghost post route (BlogPostPage
#: renders the CMS content inside the platform shell).
SITE_FEED_POST_URL: str = "https://rugmunch.io/blog/{slug}"


def site_news_line() -> str:
    """Exact rugmunch.io/news link line for news-family posts (HTML-safe)."""
    return f'📰 <a href="{SITE_NEWS_URL}">rugmunch.io/news</a> — full coverage on the site'


def feed_site_line(slug: str = "") -> str:
    """Exact feed.rugmunch.io link line (deep link when a slug exists)."""
    url = SITE_FEED_POST_URL.format(slug=slug) if slug else SITE_FEED_URL
    label = "rugmunch.io" + (f"/blog/{slug}" if slug else "/feed")
    return f'📖 <a href="{url}">{label}</a> — on the site'


def has_site_link(text: str) -> bool:
    """True when the text already carries a rugmunch.io / feed.rugmunch.io link."""
    return "rugmunch.io/news" in text or "feed.rugmunch.io" in text


def enforce_site_link(text: str, *, site: str = "news", slug: str = "") -> str:
    """Append the exact site link line when *text* lacks one (pure, never raises).

    ``site="news"`` appends the rugmunch.io/news line; ``site="feed"``
    appends the feed.rugmunch.io line (with a deep link when *slug* is
    given). Idempotent: text already carrying a site link is returned
    unchanged.
    """
    if not text or not text.strip():
        return text
    if has_site_link(text):
        return text
    line = feed_site_line(slug) if site == "feed" else site_news_line()
    return f"{text.rstrip()}\n\n{line}"
