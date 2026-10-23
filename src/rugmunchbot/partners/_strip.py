"""Append the partner strip to a finished post exactly once."""

from __future__ import annotations

from ._render import STRIP_MARK, render_strip
from ._select import max_links, select_partners


def with_partner_strip(
    text: str, *, lane: str | None, chain: str = "", token: str = "", venue: str = ""
) -> str:
    """Return *text* plus the lane's partner strip.

    Never fabricates: no available link means the text is returned unchanged.
    Idempotent — a post that already carries the strip is untouched, and a
    venue whose exact URL already appears in the body is not linked twice.
    """
    if not text or not text.strip() or STRIP_MARK in text:
        return text
    refs = [
        ref
        for ref in select_partners(lane, chain=chain, token=token, venue=venue)
        if ref.url and ref.url not in text
    ][: max_links()]
    strip = render_strip(refs)
    if not strip:
        return text
    return f"{text.rstrip()}\n\n{strip}"
