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


def _pick_image_url(value: object) -> str:
    """Return *value* when it is a usable http(s) image URL, else ``""``."""
    if isinstance(value, str) and value.strip():
        url = value.strip()
        if url.startswith(("http://", "https://")):
            return url
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


def _first_sentence(text: str) -> str:
    """Return the first sentence of *text* as a one-line hook (≤160 chars).

    The hook is the compelling one-liner that sits directly under a solo /
    hybrid lead headline (requirement: "rewritten title + a one-line hook",
    not a bare link). Feed it already-flattened plain text (``_strip_markup``);
    callers escape the result once at insertion time. Empty input → ``""``.
    Never raises.
    """
    text = (text or "").strip()
    if not text:
        return ""
    for delim in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        idx = text.find(delim)
        if idx != -1:
            candidate = text[: idx + 1].strip()
            if candidate:
                text = candidate
            break
    return text[:_FEATURE_HOOK_CHARS].rstrip()


def _lede_after_hook(summary: str, hook: str) -> str:
    """The rest of *summary* after *hook*, clipped to the feature-lede limit."""
    rest = summary
    if hook:
        rest = rest[len(hook) :].lstrip(" .")
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
            rest = text[len(prefix):]
            # Glued case ("Key takeawaysBitcoin") → drop the label entirely.
            if rest and not rest[0].isspace():
                text = rest.lstrip()
                lowered = text.lower().lstrip()
            else:
                text = rest.lstrip(" :–-")
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
    return f"{text}\n\n🔗 <a href=\"{html.escape(fallback, quote=True)}\">{_esc(label)}</a>"
