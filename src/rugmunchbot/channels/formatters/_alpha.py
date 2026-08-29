from __future__ import annotations

import html
from contextlib import suppress
from typing import Any

from rugmunchbot.channels.formatters._alerts import (
    _explorer_url,
    _item_links,
    _scan_reason_bits,
)
from rugmunchbot.channels.formatters._primitives import (
    _ALPHA_TAKEAWAY_CHARS,
    NewsPost,
    _clip,
    _esc,
    _is_placeholder,
    _now_stamp,
    _pick_image_url,
    _risk_emoji,
    _scan_channel_shill,
    _takeaway,
    site_news_line,
)
from rugmunchbot.formatters import sep, short_addr
from rugmunchbot.formatters._scan_report_riskreport import format_risk_report_block


def format_scan_card(item: dict[str, Any]) -> NewsPost:
    """Engaging @rmiscans card with full address + bot instructions + cross-channel promo.

    One token per card (the scans job posts each notable scan as its own
    card, ~15-20/day). The header reads "Suspicious Tokens & Scans of Interest"
    and every card carries the FULL token address (never truncated), a clear
    call-to-action to /scan with @RugMunchBot, chart/rugmap links, and a
    compact cross-channel shill block so readers discover the full RugMunch
    ecosystem. The reasons line surfaces the ACTUAL why a token is risky —
    honeypot / bundle / fake volume / LP removed / flagged deployer
    (``_scan_reason_bits``) — instead of the generic "high-risk verdict";
    the optional ``commentary`` key renders the one-line AI wit when present
    (fail-open to no line). Name/symbol render honestly: no "Unknown (???)"
    when only one of them is known. The score line renders the numeric risk
    (or falls back to the risk level when the score is absent),
    and the scan deep link always points at rugmunch.io/scanner. Carries the
    item's image when one exists (text-only otherwise — fail-open).
    """
    raw_name = str(item.get("name") or "").strip()
    raw_symbol = str(item.get("symbol") or "").strip()
    name = "" if _is_placeholder(raw_name) else raw_name
    symbol = "" if _is_placeholder(raw_symbol) else raw_symbol
    if name and symbol and symbol.lower() != name.lower():
        label = f"{_esc(name)} ({_esc(symbol)})"
    elif symbol:
        label = _esc(symbol)
    elif name:
        label = _esc(name)
    else:
        label = "Unnamed token"
    addr = str(item.get("address") or item.get("token_address") or "")
    chain = _esc(item.get("chain") or "")
    risk = str(item.get("risk_level") or "unknown").lower()
    score = item.get("risk_score")
    score_line = ""
    if score is not None:
        with suppress(TypeError, ValueError):
            score_line = f"{_risk_emoji(risk)} <b>Risk Score: {float(score):.0f}/100</b> \u2014 {risk.upper()}"
    if not score_line:
        score_line = f"{_risk_emoji(risk)} <b>Verdict: {risk.upper()}</b>"
    lines = [
        "\U0001f52c <b>Suspicious Tokens &amp; Scans of Interest</b>",
        "<i>Notable tokens flagged by the RugMunch scanner \u2014 always /scan before buying.</i>",
        sep(),
        "",
    ]
    chain_part = f" \u00b7 {chain}" if chain else ""
    lines.append(f"{_risk_emoji(risk)} <b>{label}</b>{chain_part}")
    # FULL address — never truncated so users can copy-paste to /scan
    if addr:
        lines.append(f"<code>{_esc(addr)}</code>")
    lines.append("")
    lines.append(score_line)
    reasons = _scan_reason_bits(item)
    if reasons:
        lines.append("\u26a0\ufe0f " + " \u00b7 ".join(_esc(r) for r in reasons))
    # ── Deep-analysis section (CONTENT-STRATEGY-CANONICAL v2) ──
    # Deployer / holders / bundle-clustering: render whatever the scanner
    # populated; missing sections show honestly as unavailable.
    dep = item.get("deployer") or {}
    holders = item.get("holders") or {}
    bundles = item.get("bundles") or {}
    deep: list[str] = []
    if isinstance(dep, dict) and dep:
        d_addr = str(dep.get("address") or "").strip()
        d_prev = dep.get("previous_tokens")
        d_rug = dep.get("rug_count", dep.get("rugs"))
        if d_addr or d_prev is not None:
            parts = ["<b>Deployer:</b>"]
            if d_addr:
                parts.append(f"<code>{_esc(d_addr)}</code>")
            if d_prev is not None:
                parts.append(f"past tokens: {_esc(d_prev)}")
            if d_rug is not None:
                parts.append(f"rugs: {_esc(d_rug)}")
            deep.append(" ".join(parts))
    if isinstance(holders, dict) and holders:
        h_count = holders.get("count", holders.get("total"))
        h_top = holders.get("top10_pct", holders.get("top10"))
        h_fresh = holders.get("fresh_pct", holders.get("fresh_wallets_pct"))
        if h_count is not None:
            seg = f"<b>Holders:</b> {_esc(h_count)}"
            if h_top is not None:
                seg += f" \u00b7 top-10 {_esc(h_top)}"
            if h_fresh is not None:
                seg += f" \u00b7 fresh {_esc(h_fresh)}"
            deep.append(seg)
    if isinstance(bundles, dict) and bundles:
        b_pct = bundles.get("bundled_pct", bundles.get("bundled_supply_pct"))
        b_clusters = bundles.get("clusters", bundles.get("cluster_count"))
        if b_pct is not None or b_clusters is not None:
            seg = "<b>Bundling:</b>"
            if b_pct is not None:
                seg += f" {_esc(b_pct)} supply bundled"
            if b_clusters is not None:
                seg += f" \u00b7 {_esc(b_clusters)} clusters"
            deep.append(seg)
    if deep:
        lines.append("")
        lines.extend(deep)
    else:
        lines.append("<i>Deep analysis (deployer/holders/bundles): unavailable for this scan</i>")
    # ── Full-product RiskReport block (deployer / holders / first-buyer /
    #    top-25 / fresh% / CEX% / bundle / cluster / honeypot / liquidity +
    #    ~30-factor score + rug/scam determination) — the same RiskReport the
    #    /scan formatter renders (rugmunch_scanner/unified/risk_report.py),
    #    so suspicious-token cards demonstrate the full scanner. Fail-open. ──
    if addr:
        risk_report_lines = format_risk_report_block(item, deep=False)
        if risk_report_lines:
            lines.append("")
            lines.extend(risk_report_lines)
    commentary = str(item.get("commentary") or "").strip()
    if commentary:
        lines.append(f"\U0001f4ac \u201c{_esc(commentary)}\u201d")
    links = _item_links(item, addr=addr, chain=str(item.get("chain") or ""))
    if links:
        lines.append("")
        # Add bot scan command — this is the KEY hook to get users on @RugMunchBot
        scan_cmd = f"/scan <code>{_esc(addr)}</code>"
        lines.append(f"\U0001f916 <b>Scan with @RugMunchBot:</b> {scan_cmd}")
        lines.append(links)
    lines.extend(["", f"\U0001f550 {_now_stamp()}"])
    lines.append(sep())
    lines.append(_scan_channel_shill())
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = _pick_image_url(item.get("image_url"))
    return post


def _fmt_usd(value: Any) -> str:
    with suppress(TypeError, ValueError):
        return f"${float(value):,.0f}"
    return ""


def _fmt_age_hours(value: Any) -> str:
    with suppress(TypeError, ValueError):
        hours = float(value)
        if hours < 1:
            return f"⏱ {int(hours * 60)}m old"
        return f"⏱ {hours:.0f}h old"
    return ""


def _fmt_wallet_short(addr: str) -> str:
    return short_addr(addr) if addr else "?"


def _launch_verdict(launch: dict[str, Any]) -> str:
    """One-line "why it matters" for a launch, honest to its scan verdict."""
    risk = str(launch.get("risk_level") or "unknown").lower()
    if risk == "critical":
        return "critical scan verdict — skip unless you know exactly what you are doing"
    if risk == "high":
        return "high scan verdict — verify liquidity and holders before sizing"
    if risk == "medium":
        return "medium scan verdict — check liquidity and holder concentration"
    if risk == "low":
        return "low scan verdict — still verify before buying"
    return "new launch — verify before buying"


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

    if whale_moves:
        lines.append("\u26a1 <b>Whale Moves</b>")
        for move in whale_moves[:4]:
            title = _esc(move.get("title") or "Whale move")
            chain = _esc(move.get("chain") or "solana")
            addr = str(move.get("token_address") or "")
            lines.append(f"\u2022 {title} ({chain})")
            body = _esc(_takeaway(move.get("body") or move.get("summary"), _ALPHA_TAKEAWAY_CHARS))
            if body:
                lines.append(f"   \U0001f4ac {body}")
            if addr:
                lines.append(f"   \U0001f916 /scan <code>{_esc(addr)}</code>")
            links = _item_links(move, addr=addr, chain=str(move.get("chain") or "solana"))
            if links:
                lines.append(f"   {links}")
        lines.append("")

    if smart_money:
        lines.append("\U0001f9e0 <b>Smart Money Wallets</b>")
        for w in smart_money[:5]:
            label = _esc(w.get("label") or _fmt_wallet_short(str(w.get("address") or "")))
            addr = str(w.get("address") or "")
            pnl = _fmt_usd(w.get("pnl_usd") or w.get("realized_pnl"))
            bits = [f"<code>{_esc(addr)}</code>"]
            if pnl:
                bits.append(f"P&L {pnl}")
            lines.append(f"\u2022 <b>{label}</b> \u2014 " + " \u00b7 ".join(bits))
            tags = [str(t) for t in (w.get("tags") or []) if str(t).strip()]
            if tags:
                lines.append("   \U0001f3f7 " + " \u00b7 ".join(_esc(t) for t in tags[:4]))
            explorer = _explorer_url(addr, str(w.get("chain") or "solana"))
            if explorer:
                lines.append(
                    f'   \U0001f9ed <a href="{html.escape(explorer, quote=True)}">Explorer</a>'
                )
        lines.append("")

    if launches:
        lines.append("\U0001f195 <b>New Launches</b>")
        for launch in launches[:5]:
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
        lines.append("")

    if not whale_moves and not smart_money and not launches and not kol_trades:
        lines.append("No fresh alpha signals in this window \u2014 checking again soon.")
        lines.append("")

    if kol_trades:
        lines.append("\U0001f4e2 <b>KOL Trades / Analyst Calls</b>")
        for k in kol_trades[:3]:
            title = _esc(k.get("title") or "KOL signal")
            url = str(k.get("url") or "").strip()
            if url:
                lines.append(f'\u2022 <a href="{html.escape(url, quote=True)}">{title}</a>')
            else:
                lines.append(f"\u2022 {title}")
            snippet = _esc(_takeaway(k.get("source"), 160))
            if snippet:
                lines.append(f"   \U0001f4ac {snippet}")
        lines.append("")

    lines.append(f"\U0001f550 {_now_stamp()}")
    lines.append(site_news_line())
    lines.append(sep())
    lines.append(_scan_channel_shill())
    post = NewsPost(_clip("\n".join(lines)))
    for item in [*whale_moves[:4], *smart_money[:5], *launches[:5]]:
        candidate = _pick_image_url(item.get("image_url"))
        if candidate:
            post.image_url = candidate
            break
    return post
