"""Smart-money / launches / KOL section rendering for the alpha digest.

Pure rendering only. Split out of ``_alpha.py`` (2026-10-23).
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.channels.formatters._alerts import _explorer_url, _item_links
from rugmunchbot.channels.formatters._alpha._lines import (
    _fmt_age_hours,
    _fmt_usd,
    _fmt_wallet_short,
    _launch_verdict,
)
from rugmunchbot.channels.formatters._primitives import (
    _esc,
    _risk_emoji,
    _takeaway,
)

_MAX_SMART_MONEY = 5
_MAX_LAUNCHES = 5
_MAX_KOL = 3
_MAX_TAGS = 4
_KOL_SNIPPET_CHARS = 160


def render_smart_money(smart_money: list[dict[str, Any]]) -> list[str]:
    """Smart-money wallet block (header + items); empty list when no data."""
    if not smart_money:
        return []
    lines = ["\U0001f9e0 <b>Smart Money Wallets</b>"]
    for w in smart_money[:_MAX_SMART_MONEY]:
        label = _esc(w.get("label") or _fmt_wallet_short(str(w.get("address") or "")))
        addr = str(w.get("address") or "")
        pnl = _fmt_usd(w.get("pnl_usd") or w.get("realized_pnl"))
        bits = [f"<code>{_esc(addr)}</code>"]
        if pnl:
            bits.append(f"P&L {pnl}")
        lines.append(f"\u2022 <b>{label}</b> \u2014 " + " \u00b7 ".join(bits))
        tags = [str(t) for t in (w.get("tags") or []) if str(t).strip()]
        if tags:
            lines.append("   \U0001f3f7 " + " \u00b7 ".join(_esc(t) for t in tags[:_MAX_TAGS]))
        explorer = _explorer_url(addr, str(w.get("chain") or "solana"))
        if explorer:
            lines.append(
                f'   \U0001f9ed <a href="{html.escape(explorer, quote=True)}">Explorer</a>'
            )
    return lines


def render_launches(launches: list[dict[str, Any]]) -> list[str]:
    """New-launch block (header + items); empty list when no data."""
    if not launches:
        return []
    lines = ["\U0001f195 <b>New Launches</b>"]
    for launch in launches[:_MAX_LAUNCHES]:
        symbol = _esc(launch.get("symbol") or "???")
        name = _esc(launch.get("name") or symbol)
        chain = _esc(launch.get("chain") or "")
        risk = _esc(launch.get("risk_level") or "unknown")
        addr = str(launch.get("address") or "")
        chain_part = f" \u2014 {chain}" if chain else ""
        lines.append(f"\u2022 {_risk_emoji(risk)} <b>{name}</b> ({symbol}){chain_part}")
        lines.append(f"   \U0001f4a1 {_esc(_launch_verdict(launch))}")
        meta = []
        age = _fmt_age_hours(launch.get("age_hours"))
        if age:
            meta.append(age)
        liq = _fmt_usd(launch.get("liquidity_usd") or launch.get("liquidity"))
        if liq:
            meta.append(f"\U0001f4a7 {liq} liq")
        if meta:
            lines.append("   " + " \u00b7 ".join(meta))
        if addr:
            lines.append(f"   \U0001f916 /scan <code>{_esc(addr)}</code>")
        links = _item_links(launch, addr=addr, chain=str(launch.get("chain") or ""))
        if links:
            lines.append(f"   {links}")
    return lines


def render_kol_trades(kol_trades: list[dict[str, Any]] | None) -> list[str]:
    """KOL trades / analyst calls block; empty list when no data."""
    if not kol_trades:
        return []
    lines = ["\U0001f4e2 <b>KOL Trades / Analyst Calls</b>"]
    for k in kol_trades[:_MAX_KOL]:
        title = _esc(k.get("title") or "KOL signal")
        url = str(k.get("url") or "").strip()
        if url:
            lines.append(f'\u2022 <a href="{html.escape(url, quote=True)}">{title}</a>')
        else:
            lines.append(f"\u2022 {title}")
        snippet = _esc(_takeaway(k.get("source"), _KOL_SNIPPET_CHARS))
        if snippet:
            lines.append(f"   \U0001f4ac {snippet}")
    return lines
