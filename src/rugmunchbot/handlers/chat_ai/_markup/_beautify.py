"""chat_ai v10 response beautifier — header normalization + thin separators.

Runs on the RAW LLM answer BEFORE chunking/escaping. Extracted verbatim from
the former single-module ``chat_ai/_markup.py``.
"""

from __future__ import annotations

import re

from rugmunchbot.formatters import thin_sep
from rugmunchbot.handlers.chat_ai._markup._constants import (
    _SECTION_EMOJIS,
    _SECTION_HEADER_MAX,
)


def _last_sentence_boundary(text: str, limit: int) -> int:
    """Index just past the last sentence-ending punctuation before ``limit``.

    Returns -1 when no sentence boundary exists inside the window. Used by
    :func:`_beautify_answer` so answer caps land at sentence ends instead of
    mid-sentence ("split at natural section boundaries, not mid-sentence").
    """
    best = -1
    for sep_ in (". ", "? ", "! "):
        idx = text.rfind(sep_, 0, limit)
        if idx >= 0:
            best = max(best, idx + len(sep_))
    if best > limit:
        return limit  # boundary punctuation ends exactly at the window edge
    return best


def _is_section_header(line: str) -> bool:
    """True for a short line that begins with a section emoji (v10 header).

    A line starting with an emoji but longer than ``_SECTION_HEADER_MAX`` is
    prose, not a header — e.g. "📊 This token's chart broke out of a wedge
    that's been building since March" stays content. Prefix matching handles
    multi-code-point emojis (🛡️ = U+1F6E1 + VS16).

    What follows the emoji must be a TITLE, not data: a line containing a
    pipe (``💰 $0.0001 | 24h: +45%`` — a ``_format_tool_answer`` price line)
    or digit-heavy content (``📈 MCap: $50,000,000``) is a tool answer, never
    a header — only pure emoji + short non-numeric title lines are headers.
    """
    core = line.strip().lstrip("*").strip()
    if not core or len(core) > _SECTION_HEADER_MAX:
        return False
    for emoji in _SECTION_EMOJIS:
        if core.startswith(emoji):
            rest = core[len(emoji) :].strip()
            if "|" in rest:
                return False
            digits = sum(ch.isdigit() for ch in rest)
            letters = sum(ch.isalpha() for ch in rest)
            return digits <= letters
    return False


def _unwrap_bold(line: str) -> str:
    """Strip a balanced ``**...**``/``***...***`` wrapper (or leading `*`)."""
    stripped = line.strip()
    m = re.match(r"^\*{2,3}(.+?)\*{2,3}$", stripped)
    if m:
        return m.group(1).strip()
    return re.sub(r"^\*{1,3}", "", stripped).strip()


# Separator lines the model sometimes emits under a header (──── / ━━━━).
# Deduped against the thin_sep() the beautifier already inserts.
_SEPARATOR_LINE_RE = re.compile(r"^[\u2500\u2501\u2550\u2551\-_]{3,}$")
# Base emoji code points (excludes ZWJ so compound/role emoji are untouched);
# an optional VS16 is folded into the collapsed unit so 🛡️🛡️ → 🛡️ correctly.
_EMOJI_BASE = "\U0001f000-\U0001faff\u2600-\u27bf"
_LEADING_EMOJI_RUN_RE = re.compile(f"^([{_EMOJI_BASE}]\ufe0f?)(?:\\s*\\1)+")
# ALL-CAPS abbreviations that must survive title-casing (TA, RSI, MACD...).
_HEADER_ABBR = frozenset(
    {
        "TA",
        "RSI",
        "MACD",
        "EMA",
        "USD",
        "BTC",
        "ETH",
        "SOL",
        "BNB",
        "S/R",
        "DYOR",
        "AEGIS",
        "TVL",
        "ATH",
        "ATL",
        "HHI",
        "LP",
        "CEX",
        "DEX",
        "MEV",
        "AI",
        "NFT",
        "KOL",
        "P&L",
        "P/L",
        "GM",
        "GN",
        "X2",
    }
)
# Connector words in ALL-CAPS headers render lowercase ("TA and RSI").
_HEADER_CONNECTORS = frozenset({"AND", "OF", "THE", "FOR", "ON", "IN", "TO", "VS", "WITH"})


def _is_separator_line(line: str) -> bool:
    """True for a line that is only box-drawing dashes (model-emitted sep)."""
    return bool(_SEPARATOR_LINE_RE.match(line.strip()))


def _collapse_leading_emoji(text: str) -> str:
    """Collapse a run of identical emojis at the start of a header.

    ``📊📊📊 Market Read`` / ``📊 📊 Market Read`` → ``📊 Market Read``.
    The optional variation selector (VS16) is part of the collapsed unit so
    compound emoji like 🛡️ (U+1F6E1 + VS16) dedupe to 🛡️ — never 🛡.
    """
    return _LEADING_EMOJI_RUN_RE.sub(lambda m: m.group(1), text)


def _normalize_header_casing(text: str) -> str:
    """Best-effort header casing (v12): first word capitalized, ALL-CAPS
    prose title-cased, real abbreviations (TA/RSI/MACD/USD...) preserved.

    ``📊 market read`` → ``📊 Market read``; ``📊 MARKET READ`` →
    ``📊 Market Read``; ``📊 TA AND RSI`` → ``📊 TA and RSI``. Mixed-case
    headers are never rewritten, so proper nouns and $SYMBOLS survive.
    """
    words = text.split()
    if not words:
        return text
    # Leading pure-emoji tokens (📊 / 🛡️ / …) are not words — casing applies
    # to the first real word, and the emoji prefix is preserved verbatim.
    lead: list[str] = []
    while words and not any(ch.isalnum() for ch in words[0]):
        lead.append(words.pop(0))
    if not words:
        return text
    head = words[0]
    if head.islower() and head[0].isalpha():
        words[0] = head[0].upper() + head[1:]
    stripped = "".join(ch for ch in " ".join(words) if ch.isalpha())
    if len(stripped) >= 4 and stripped.isupper():
        words = [
            w.lower()
            if w in _HEADER_CONNECTORS
            else w
            if w in _HEADER_ABBR
            else (w[0].upper() + w[1:].lower())
            if w.isalpha()
            else w
            for w in words
        ]
    return " ".join(lead + words)


def _beautify_answer(answer: str) -> str:
    """v10 beautifier — normalize headers + insert thin separators (raw text).

    - Lines that look like section headers (start with a section emoji,
      short) are normalized to ``**emoji Title**`` on their own line, with a
      blank line before them so the chunker sees a clean section boundary.
    - ``thin_sep()`` is inserted beneath every header for visual rhythm
      (📊 header / ──── / content, like the /scan report blocks).
    - Model-emitted separator lines under a header are deduped (never two
      ``────`` in a row), repeated leading emojis collapse (no emoji spam),
      and header casing is normalized conservatively (v12).
    - All other lines are preserved verbatim (code blocks, lists, spacing).
    Markdown ``**bold**`` is left in place for :func:`_markdown_to_html`
    which runs AFTER escaping in :func:`_send_answer`.
    """
    lines = answer.split("\n")
    out: list[str] = []
    prev_empty = True
    for raw_line in lines:
        if not raw_line.strip():
            out.append("")
            prev_empty = True
            continue
        if _is_section_header(raw_line):
            if not prev_empty:
                out.append("")
            header_text = _collapse_leading_emoji(_unwrap_bold(raw_line))
            header_text = _normalize_header_casing(header_text)
            out.append(f"**{header_text}**")
            out.append(thin_sep())
            prev_empty = False
            continue
        if _is_separator_line(raw_line):
            # The model put a separator under a header we already decorated —
            # skip it instead of stacking a second dashed line.
            if out and _is_separator_line(out[-1]):
                continue
            out.append(raw_line)
            prev_empty = False
            continue
        out.append(raw_line)
        prev_empty = False
    start = 0
    while start < len(out) and not out[start]:
        start += 1
    out = out[start:]
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)
