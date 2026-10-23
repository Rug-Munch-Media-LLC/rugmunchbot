"""Render the partner strip (HTML) for a lane — pure string building."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from rugmunchbot.referrals import DISCLOSURE

from ._catalog import display_name
from ._select import select_partners

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rugmunchbot.referrals import Referral

#: Presence marker — makes the append idempotent across retries.
STRIP_MARK = "\U0001f91d <b>Partners</b>"


def render_strip(refs: Sequence[Referral]) -> str:
    """One compact strip: linked venue names plus the single disclosure line."""
    links = " \u00b7 ".join(
        f'<a href="{html.escape(str(ref.url), quote=True)}">'
        f"{html.escape(display_name(ref.platform))}</a>"
        for ref in refs
        if ref.url
    )
    if not links:
        return ""
    return f"{STRIP_MARK}: {links}\n{DISCLOSURE}"


def partner_strip(lane: str | None, *, chain: str = "", token: str = "", venue: str = "") -> str:
    """Strip for a lane ("" when disabled or no real link exists)."""
    return render_strip(select_partners(lane, chain=chain, token=token, venue=venue))
