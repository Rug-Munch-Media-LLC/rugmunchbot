"""Longform lexical builder — markdown → Ghost lexical JSON.

Split from ``channels._longform`` (which owns the Telegram-HTML → markdown
transform) so each module stays under the 200-line cap. ``to_lexical`` runs
``to_markdown`` first, then builds the Ghost lexical document: ``#`` → h1,
``##`` → h2, ``###`` → h3, ``- `` runs → bullet lists, everything else →
paragraphs. Returns a JSON string ready for ``create_post``'s ``lexical``.
"""

from __future__ import annotations

import json
import re

from rugmunchbot.channels._longform import _ATX_RE, _BULLET_RE, to_markdown

#: Markdown inline: ``[text](url)``, ``**bold**``, ``*italic*``.
_INLINE_MD_RE = re.compile(
    r"\[([^\]]+)\]\(([^)]+)\)"
    r"|\*\*([^*]+)\*\*"
    r"|(?<!\*)\*([^*\n]+)\*(?!\*)"
)


def _text_node(text: str, fmt: int = 0) -> dict:
    return {
        "detail": 0,
        "format": fmt,
        "mode": "normal",
        "style": "",
        "text": text,
        "type": "text",
        "version": 1,
    }


def _link_node(text: str, url: str) -> dict:
    return {
        "type": "link",
        "version": 1,
        "rel": None,
        "target": None,
        "title": None,
        "url": url,
        "children": [_text_node(text)],
    }


def _inline_children(text: str) -> list[dict]:
    """Parse markdown inline (links/bold/italic) into lexical child nodes."""
    children: list[dict] = []
    pos = 0
    for m in _INLINE_MD_RE.finditer(text):
        if m.start() > pos:
            children.append(_text_node(text[pos : m.start()]))
        if m.group(1) is not None:
            children.append(_link_node(m.group(1), m.group(2)))
        elif m.group(3) is not None:
            children.append(_text_node(m.group(3), fmt=2))
        elif m.group(4) is not None:
            children.append(_text_node(m.group(4), fmt=1))
        pos = m.end()
    if pos < len(text):
        children.append(_text_node(text[pos:]))
    return children or [_text_node("")]


def _heading_node(tag: str, text: str) -> dict:
    return {
        "type": "heading",
        "version": 1,
        "children": _inline_children(text),
        "direction": "ltr",
        "format": "",
        "indent": 0,
        "tag": tag,
    }


def _paragraph_node(text: str) -> dict:
    return {
        "type": "paragraph",
        "version": 1,
        "children": _inline_children(text),
        "direction": "ltr",
        "format": "",
        "indent": 0,
        "textFormat": 0,
        "textStyle": "",
    }


def _list_node(items: list[str]) -> dict:
    return {
        "type": "list",
        "version": 1,
        "listType": "bullet",
        "start": 1,
        "direction": "ltr",
        "format": "",
        "indent": 0,
        "mode": "normal",
        "style": "",
        "tag": "ul",
        "children": [
            {
                "type": "listitem",
                "version": 1,
                "children": _inline_children(it),
                "direction": "ltr",
                "format": "",
                "indent": 0,
                "mode": "normal",
                "style": "",
                "value": 1,
            }
            for it in items
        ],
    }


def to_lexical(report: str) -> str:
    """Transform a Telegram-HTML report into a Ghost lexical JSON string."""
    md = to_markdown(report)
    children: list[dict] = []
    for block in re.split(r"\n\s*\n", md):
        block = block.strip()
        if not block:
            continue
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        atx = _ATX_RE.match(lines[0])
        if atx:
            children.append(_heading_node(f"h{len(atx.group(1))}", atx.group(2)))
            lines = lines[1:]
            if not lines:
                continue
        if all(_BULLET_RE.match(ln) for ln in lines):
            children.append(_list_node([_BULLET_RE.sub("", ln) for ln in lines]))
            continue
        for ln in lines:
            children.append(_paragraph_node(ln))
    return json.dumps(
        {
            "root": {
                "children": children,
                "direction": "ltr",
                "format": "",
                "indent": 0,
                "type": "root",
                "version": 1,
            }
        }
    )


__all__ = ["to_lexical"]
