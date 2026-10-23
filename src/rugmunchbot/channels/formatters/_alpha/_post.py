"""Alpha digest post assembly.

Split out of ``_alpha.py`` (2026-10-23). Pure rendering — no network I/O.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.channels.formatters._alpha._move import render_whale_moves
from rugmunchbot.channels.formatters._alpha._sections import (
    render_kol_trades,
    render_launches,
    render_smart_money,
)
from rugmunchbot.channels.formatters._primitives import (
    NewsPost,
    _clip,
    _graphic_for,
    _now_stamp,
    _pick_image_url,
    _scan_channel_shill,
    site_news_line,
)
from rugmunchbot.formatters import sep

_MAX_ALPHA_WHALES = 4
_MAX_ALPHA_SMART_MONEY = 5
_MAX_ALPHA_LAUNCHES = 5


def format_alpha_post(
    whale_moves: list[dict[str, Any]],
    smart_money: list[dict[str, Any]],
    launches: list[dict[str, Any]],
    kol_trades: list[dict[str, Any]] | None = None,
) -> NewsPost:
    """Alpha digest: whale moves + smart money wallets + new launches + KOL trades.

    High-signal voice: every item leads with the signal, adds the context
    that matters (whale-move body hook, wallet tags, launch age/liquidity),
    a one-line verdict, and actionable scan/chart/explorer links. Every
    section only renders when its live source returned data — an empty
    section is omitted, never invented. The optional ``kol_trades`` rail
    (live SearXNG X-signal hits) renders each hit with its SOURCE LINK —
    cite-or-omit. Every post ends with a bot CTA so readers know to /scan
    with @RugMunchBot. Carries the first usable image on the returned
    ``NewsPost`` so the scheduler can attach it as a photo.
    """
    lines = [
        "\U0001f40b <b>Alpha Feed</b>",
        "<i>Whale moves, smart money, new launches \u2014 /scan with @RugMunchBot before you ape.</i>",
        sep(),
        "",
    ]

    rendered_whales = render_whale_moves(whale_moves)
    if rendered_whales:
        lines.append("\u26a1 <b>Whale Moves</b>")
        lines.extend(rendered_whales)
        lines.append("")

    for section in (render_smart_money(smart_money), render_launches(launches)):
        if section:
            lines.extend(section)
            lines.append("")

    # Empty-window honesty: render the fallback only when NO section actually
    # produced content. A whale list whose only move was dropped (wallet-less)
    # does NOT keep the section header, and a fully-empty digest says so.
    if not rendered_whales and not smart_money and not launches and not kol_trades:
        lines.append("No fresh alpha signals in this window \u2014 checking again soon.")
        lines.append("")

    kol_section = render_kol_trades(kol_trades)
    if kol_section:
        lines.extend(kol_section)
        lines.append("")

    lines.append(f"\U0001f550 {_now_stamp()}")
    lines.append(site_news_line())
    lines.append(sep())
    lines.append(_scan_channel_shill())
    post = NewsPost(_clip("\n".join(lines)))
    for item in [
        *whale_moves[:_MAX_ALPHA_WHALES],
        *smart_money[:_MAX_ALPHA_SMART_MONEY],
        *launches[:_MAX_ALPHA_LAUNCHES],
    ]:
        candidate = _pick_image_url(item.get("image_url"))
        if candidate:
            post.image_url = candidate
            break
    if not post.image_url:
        post.image_url = _graphic_for("alpha")
    return post
