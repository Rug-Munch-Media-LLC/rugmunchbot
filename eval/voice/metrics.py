"""Raw measurements for one piece of bot output — numbers, never judgements."""

from __future__ import annotations

import re
import statistics

import rules

_TAG = re.compile(r"<[^>]+>")
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_FACT = re.compile(r"\d+[.,]?\d*\s?%|\$\s?\d|\b\d+/\d+\b|\b\d+(?:\.\d+)?[KMB]\b")
_URL = re.compile(r"https?://\S+")
_EMOJI = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\u2b00-\u2bff\u2139\u2049\u203c]")
_HEDGE = re.compile(r"\b(" + "|".join(re.escape(h) for h in rules.HEDGES) + r")\b")


def measure(text: str) -> dict:
    """Measure one message: length spread, emoji, phrase hits, structure legs."""
    plain = _TAG.sub("", text)
    low = plain.lower()
    sentences = [s for s in _SPLIT.split(plain) if s.strip()]
    lens = [n for n in (len(_WORD.findall(s)) for s in sentences) if n]
    spread = statistics.pstdev(lens) if len(lens) > 1 else 0.0
    lines = [line.strip() for line in plain.splitlines() if line.strip()]
    head = lines[0] if lines else ""
    return {
        "words": len(_WORD.findall(plain)),
        "sentences": len(sentences),
        "spread": round(spread, 2),
        "emoji": len(_EMOJI.findall(plain)),
        "exclaim": plain.count("!"),
        "banned": [p for p in rules.BANNED_PHRASES if p in low],
        "cliches": [p for p in rules.CLICHES if p in low],
        "hedges": sorted({m.group(0) for m in _HEDGE.finditer(low)}),
        "jokes": [p for p in rules.JOKE_MARKERS if p in low],
        "headline": bool(head) and len(head) <= 100,
        "fact": bool(_FACT.search(plain)),
        "cta": bool(_URL.search(plain)) or any(c in low for c in rules.CTA_MARKERS),
    }
