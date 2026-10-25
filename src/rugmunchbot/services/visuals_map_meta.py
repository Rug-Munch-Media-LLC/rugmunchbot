"""RugMap PNG metadata strip — token, chain, timestamp, risk, AI opinion.

``services.visuals.render_cluster_map`` is the single map engine: it draws the
bubbles, arrows, legend and branding. This module only APPENDS a metadata strip
under the finished image, so the engine file stays untouched (it is over the
size ratchet and may never grow) and there is no second renderer.

``annotate_map_png`` returns ``None`` when Pillow or the image is unusable so
the caller can still send the un-annotated map.
"""

from __future__ import annotations

import io
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi.visuals.map_meta")

_BG = (13, 17, 23)
_STRIP_BG = (18, 21, 27)
_SEP = (40, 46, 58)
_TEXT = (140, 148, 156)
_TITLE = (232, 234, 237)
_ACCENT = (88, 166, 255)
_WARN = (239, 83, 80)
_WATCH = (255, 183, 77)
_OK = (102, 187, 106)

_RISK_COLOR = {"high": _WARN, "medium": _WATCH, "low": _OK, "safe": _OK}


def _wrap(text: str, per_line: int, max_lines: int = 2) -> list[str]:
    """Wrap plain text to ``per_line`` chars, hard-capping the line count."""
    words, lines, cur = text.split(), [], ""
    for word in words:
        candidate = f"{cur} {word}".strip()
        if len(candidate) <= per_line:
            cur = candidate
            continue
        if cur:
            lines.append(cur)
        cur = word
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and len(" ".join(words)) > sum(len(x) for x in lines):
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def annotate_map_png(png: bytes, meta: dict[str, Any]) -> bytes | None:
    """Append a metadata strip to a rendered RugMap PNG.

    ``meta`` keys: ``token``, ``chain``, ``timestamp``, ``risk`` (str, e.g.
    "HIGH 74/100"), ``ai`` (str opinion). Empty ``meta`` returns the input
    unchanged; unusable input or a missing Pillow returns ``None``.
    """
    if not png:
        return None
    if not any(meta.get(k) for k in ("token", "chain", "timestamp", "risk", "ai")):
        return png
    try:
        from PIL import Image, ImageDraw, ImageFont
    except (ImportError, OSError, RuntimeError) as exc:
        logger.debug("Pillow unavailable, keeping naked rugmap png: %s", exc)
        return None

    try:
        base = Image.open(io.BytesIO(png)).convert("RGB")
    except (OSError, ValueError) as exc:
        logger.debug("rugmap png unreadable, cannot annotate: %s", exc)
        return None

    w, h = base.size
    try:
        small = ImageFont.load_default(size=15)
        tiny = ImageFont.load_default(size=13)
    except TypeError:  # Pillow < 10.1 has no sized default font
        small = tiny = ImageFont.load_default()

    ai_lines = _wrap(str(meta.get("ai") or ""), 104, 2) if meta.get("ai") else []
    strip_h = 62 + 18 * len(ai_lines)
    img = Image.new("RGB", (w, h + strip_h), _BG)
    img.paste(base, (0, 0))
    draw = ImageDraw.Draw(img)

    top = h
    draw.rectangle([0, top, w, h + strip_h], fill=_STRIP_BG)
    draw.line([(0, top), (w, top)], fill=_SEP, width=2)

    risk_txt = str(meta.get("risk") or "")
    risk_color = _RISK_COLOR.get(risk_txt.split()[0].lower() if risk_txt else "", _TEXT)
    token = str(meta.get("token") or "")
    chain = str(meta.get("chain") or "")
    draw.text((14, top + 8), f"{token}", font=small, fill=_TITLE)
    token_w = draw.textlength(token, font=small) if token else 0
    meta_line = " · ".join(x for x in (chain, str(meta.get("timestamp") or ""), risk_txt) if x)
    draw.text((14 + token_w + 12, top + 9), meta_line, font=tiny, fill=risk_color)

    y = top + 32
    for line in ai_lines:
        draw.text((14, y), f"AI: {line}", font=tiny, fill=_TEXT)
        y += 18

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


__all__ = ["annotate_map_png"]
