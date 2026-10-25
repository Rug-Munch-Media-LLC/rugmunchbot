"""Universal format law — the mandatory pre-send pass (WO-3, 2026-08-25).

Implements the FORMAT section of /root/.knowledge/CONTENT-POSTING-SPEC.md:

    FORBIDDEN: wall-of-text, bunched sentences, dictionary citations,
    mid-word truncation, repeated info across paragraphs, emoji spam.

Every channel post MUST pass through :func:`enforce_format_law` exactly once
before it reaches Telegram. The scheduler wires this into ``_safe_send`` so
no builder can bypass it. The pass is:

* **pure** — no I/O, no network, never raises (fail-open: on any internal
  error the original text is returned unchanged),
* **idempotent** — formatting already-compliant text is a no-op,
* **HTML-safe** — Telegram ``parse_mode=HTML`` tags are preserved; only
  text between tags is touched, and inserted whitespace always lands
  between text nodes, never inside a tag.

What each rule does:

1. ``_fix_truncation``      — removes mid-text "…" artifacts (a clip that
                              landed in the middle of more text) and
                              trailing whitespace; legit end-of-post "…"
                              clipping is kept.
2. ``_dedupe_blocks``       — drops repeated identical lines/paragraphs
                              (case-insensitive, keep first). Short
                              structural lines (<12 visible chars, e.g.
                              separators) are exempt.
3. ``_cap_emoji``           — collapses runs of ≥2 identical emoji to one
                              and caps same-emoji repeats at
                              ``MAX_SAME_EMOJI`` per post (spec: no emoji
                              spam; structural single-use markers stay).
4. ``_clip_headlines``      — every standalone bold headline line is
                              clipped to ≤ ``MAX_HEADLINE_CHARS`` visible
                              characters at a word boundary (spec law).
5. ``_split_walls``         — a paragraph with more than ``MAX_PARA_SENTENCES``
                              sentences and no line breaks is split into
                              ≤3-sentence paragraphs (kills walls-of-text /
                              bunched sentences).

Dictionary citations ("according to Dictionary.com"-style boilerplate) are
handled upstream by ``_strip_junk_prefixes`` in ``_primitives``; this module
additionally drops lines that ARE a bare citation label.
"""

from __future__ import annotations

import html as _html
import os
import re

from rugmunchbot.content_style._normalize import normalize
from rugmunchbot.content_style._rules import (
    MAX_HEADLINE_CHARS as _CANON_HEADLINE,
    MAX_SAME_EMOJI as _CANON_SAME_EMOJI,
)

__all__ = [
    "MAX_HEADLINE_CHARS",
    "MAX_PARA_SENTENCES",
    "MAX_SAME_EMOJI",
    "enforce_format_law",
]

#: Spec law: HEADLINE line <=80 chars (visible text, tags excluded).
MAX_HEADLINE_CHARS = _CANON_HEADLINE

#: Spec law: BODY short paragraphs — max 3 sentences per paragraph.
MAX_PARA_SENTENCES = int(os.getenv("RMI_FORMAT_MAX_PARA_SENTENCES", "3"))

#: Emoji-spam cap: the same emoji may appear at most this many times per post.
#: Structural markers are single-use by design; anything repeated harder is spam.
MAX_SAME_EMOJI = _CANON_SAME_EMOJI

#: Paragraphs shorter than this are never wall-split (avoid churn).
_WALL_MIN_CHARS = 240

#: Lines shorter than this are structural (separators/stamps) — dedupe-exempt.
_STRUCTURAL_LINE_CHARS = 12

_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&[a-zA-Z]+;|&#\d+;")
# Standalone bold headline: optional plain-text prefix (severity emoji /
# stamp — house style "🚨 <b>…</b>"), then the bold run ending the line.
# The prefix may not contain "<" so mid-prose bold is never matched.
_BOLD_LINE_RE = re.compile(r"^([^<\n]*)(<b>)(.*?)(</b>\s*)$", re.DOTALL)
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# Broad emoji coverage: pictographs, dingbats, arrows/symbols, keycaps,
# variation selectors and ZWJ sequences.
_EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"
    "\u2600-\u27bf"
    "\u2b00-\u2bff"
    "\u2190-\u21ff"
    "\u2934\u2935"
    "\ufe0f\u200d"
    "\u2049\u203c\u2139\u2122"
    "]"
)
_CITATION_LABEL_RE = re.compile(
    r"^\s*(?:source[s]?\s*:|reference[s]?\s*:|via\s+(?:dictionary|wikipedia|investopedia))"
    r".*$",
    re.IGNORECASE,
)


def _visible_len(fragment: str) -> int:
    """Visible character count of an HTML fragment (tags + entities excluded)."""
    plain = _TAG_RE.sub("", fragment)
    plain = _ENTITY_RE.sub("x", plain)  # entities render as ~1 char
    return len(_html.unescape(plain))


_TRAILING_CLOSE_TAGS_RE = re.compile(r"(?:</[a-zA-Z]+>\s*)+$")


def _fix_truncation(text: str) -> str:
    """Remove mid-text ellipsis artifacts; keep legitimate line-end clips.

    An "…" is legitimate ONLY at the visible end of its own line (a
    word-boundary clip from ``_clip`` / ``_clip_words``) — including
    immediately before closing HTML tags like ``</b>``. Anywhere else it is
    a truncation artifact from upstream slop and is removed.
    """
    lines: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        visible_tail = _TRAILING_CLOSE_TAGS_RE.sub("", line)
        if visible_tail.endswith("…"):
            lines.append(line)
        else:
            lines.append(line.replace("…", ""))
    return "\n".join(lines).strip()


def _dedupe_blocks(text: str) -> str:
    """Drop repeated identical non-structural lines (keep first occurrence)."""
    seen: set[str] = set()
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        if _visible_len(stripped) < _STRUCTURAL_LINE_CHARS:
            out.append(line)  # separators / stamps / footers stay
            continue
        if _CITATION_LABEL_RE.match(stripped):
            continue  # bare citation-label line = dictionary slop
        key = stripped.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return "\n".join(out)


def _cap_emoji(text: str) -> str:
    """Collapse consecutive identical-emoji runs; cap same-emoji repeats."""
    # Runs of the same emoji ("🔥🔥🔥") collapse to one.
    collapsed: list[str] = []
    prev = ""
    for ch in text:
        if ch == prev and _EMOJI_RE.match(ch):
            continue
        collapsed.append(ch)
        prev = ch
    out = "".join(collapsed)
    # Same emoji scattered across the post capped at MAX_SAME_EMOJI.
    counts: dict[str, int] = {}
    chars: list[str] = []
    for ch in out:
        if _EMOJI_RE.match(ch):
            n = counts.get(ch, 0)
            if n >= MAX_SAME_EMOJI:
                continue
            counts[ch] = n + 1
        chars.append(ch)
    return "".join(chars)


def _clip_headlines(text: str) -> str:
    """Clip standalone bold headline lines to MAX_HEADLINE_CHARS visible chars."""
    out: list[str] = []
    for line in text.split("\n"):
        m = _BOLD_LINE_RE.match(line)
        if not m:
            out.append(line)
            continue
        prefix, open_tag, inner, close_tag = m.group(1), m.group(2), m.group(3), m.group(4)
        if _visible_len(inner) <= MAX_HEADLINE_CHARS:
            out.append(line)
            continue
        if _visible_len(inner) <= MAX_HEADLINE_CHARS:
            out.append(line)
            continue
        # Inner may carry an <a …>label</a>; clip the LABEL only so the
        # href survives intact.
        link = re.match(r"^(\s*<a\s[^>]*>)(.*?)(</a>\s*)$", inner, re.DOTALL)
        if link:
            l_open, label, l_close = link.group(1), link.group(2), link.group(3)
            inner = l_open + _clip_words(label, MAX_HEADLINE_CHARS) + l_close
        else:
            inner = _clip_words(inner, MAX_HEADLINE_CHARS)
        out.append(prefix + open_tag + inner + close_tag)
    return "\n".join(out)


def _clip_words(text: str, limit: int) -> str:
    """Word-boundary clip to *limit* visible chars with a trailing ellipsis."""
    if _visible_len(text) <= limit:
        return text
    words = text.split(" ")
    acc: list[str] = []
    length = 0
    for w in words:
        wlen = _visible_len(w) + (1 if acc else 0)
        if length + wlen > limit - 1:
            break
        acc.append(w)
        length += wlen
    return " ".join(acc).rstrip(" ,;:-") + "…"


def _split_walls(text: str) -> str:
    """Split sentence-bunched single-block paragraphs into ≤3-sentence paras."""
    blocks = text.split("\n\n")
    fixed: list[str] = []
    for block in blocks:
        stripped = block.strip()
        if not stripped or "\n" in stripped or _visible_len(stripped) < _WALL_MIN_CHARS:
            fixed.append(block)
            continue
        plain = _TAG_RE.sub("", stripped)
        sentences = [s for s in _SENTENCE_SPLIT_RE.split(plain) if s.strip()]
        if len(sentences) <= MAX_PARA_SENTENCES:
            fixed.append(block)
            continue
        # Re-split preserving inline tags: walk the raw block and cut after
        # sentence-ending punctuation that sits outside any tag.
        chunks: list[str] = []
        current: list[str] = []
        count = 0
        idx = 0
        in_tag = False
        while idx < len(stripped):
            ch = stripped[idx]
            current.append(ch)
            if ch == "<":
                in_tag = True
            elif ch == ">":
                in_tag = False
            elif not in_tag and ch in ".!?":
                nxt = stripped[idx + 1] if idx + 1 < len(stripped) else " "
                if nxt in (" ", "<", ""):
                    count += 1
                    if count >= MAX_PARA_SENTENCES:
                        chunks.append("".join(current).strip())
                        current = []
                        count = 0
            idx += 1
        tail = "".join(current).strip()
        if tail:
            chunks.append(tail)
        fixed.append("\n\n".join(c for c in chunks if c))
    return "\n\n".join(fixed)


def enforce_format_law(text: str) -> str:
    """Mandatory pre-send pass. Returns SPEC-compliant text; never raises.

    Pipeline order matters: truncation artifacts first (they create junk
    lines), then dedupe, emoji caps, headline clips, wall splitting, and a
    final whitespace normalisation. Idempotent by construction — verify with
    ``enforce_format_law(enforce_format_law(x)) == enforce_format_law(x)``.
    """
    if not text or not text.strip():
        return text or ""
    try:
        out = _fix_truncation(text)
        out = _dedupe_blocks(out)
        out = _cap_emoji(out)
        out = _clip_headlines(out)
        out = _split_walls(out)
        # canonical layout: blank runs, bullets, banners, balanced tags
        out = normalize(out, links=False)
        return out.strip()
    except Exception:  # fail-open: a formatter bug must never block a send
        return text
