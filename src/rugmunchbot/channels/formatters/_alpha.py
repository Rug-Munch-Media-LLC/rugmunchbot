from __future__ import annotations

import html
from contextlib import suppress
from datetime import UTC, datetime
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


def _scan_signal_lines(item: dict[str, Any]) -> list[str]:
    """The actual-signal line parts for a scans-of-interest card.

    Every posted token must show WHY it is suspicious: holders count,
    liquidity amount, volume, first-25-buyers, bundle/deployer flags.
    Renders each field only when the scan payload actually reported it
    (honest-data rule — never a fabricated 0). Pure — never raises.
    """
    from rugmunchbot.formatters._scan_report_common._fields import (
        _float_or_none,
        _holders_count,
    )

    parts: list[str] = []
    holders = _holders_count(item)
    if holders is not None:
        parts.append(f"\U0001f465 <b>{holders:,}</b> holders")
    liquidity = _float_or_none(item.get("liquidity_usd") or item.get("liquidity"))
    if liquidity is not None:
        parts.append(f"\U0001f4a7 <b>${liquidity:,.0f}</b> liq")
    volume = _float_or_none(item.get("volume_24h"))
    if volume is not None:
        parts.append(f"\U0001f4c8 <b>${volume:,.0f}</b> vol 24h")
    first_buyers = item.get("first_buyers")
    cohort: int | None = None
    if isinstance(first_buyers, dict):
        buyers = first_buyers.get("buyers")
        if isinstance(buyers, list) and buyers:
            cohort = (
                first_buyers.get("limit")
                if isinstance(first_buyers.get("limit"), int)
                else len(buyers)
            )
    elif isinstance(first_buyers, list) and first_buyers:
        cohort = len(first_buyers)
    if cohort:
        parts.append(f"\U0001f6d2 first-<b>{cohort}</b> buyers")
    ba = item.get("bundle_analysis")
    if isinstance(ba, dict) and ba.get("is_bundled") is not None:
        parts.append(
            "\U0001f578\ufe0f bundle <b>DETECTED</b>"
            if ba["is_bundled"]
            else "\U0001f578\ufe0f no bundle"
        )
    deployer_addr = item.get("deployer_address")
    dp = item.get("deployer_profile")
    if not deployer_addr and isinstance(dp, dict):
        deployer_addr = dp.get("address") or dp.get("deployer_address")
    if not deployer_addr:
        legacy_dep = item.get("deployer")
        if isinstance(legacy_dep, str) and len(legacy_dep) >= 10:
            deployer_addr = legacy_dep
    if deployer_addr:
        parts.append(
            f"\U0001f468\u200d\U0001f4bb deployer <code>{_esc(short_addr(str(deployer_addr)))}</code>"
        )
    return parts


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
    # ── Scans-of-interest signal (2026-08-29 fix) ──
    # The REAL why a reader needs: holders count, liquidity amount, volume,
    # first-25-buyers, bundle/deployer flags — the actual signal, rendered
    # only when the scan reported it. A ``scan_pending`` card (deep phases
    # still running) says so honestly — "Scanning…" — and carries NO
    # RiskReport block (a thin 1/30-factor composite would contradict the
    # header). Never "Deep analysis unavailable".
    signal = _scan_signal_lines(item)
    if signal:
        lines.append("")
        lines.append("\U0001f9e0 " + " \u00b7 ".join(signal))
    if item.get("scan_pending"):
        lines.append("")
        lines.append(
            "\u23f3 <i>Scanning\u2026 deep analysis in progress \u2014 "
            "I\u2019ll repost the full report when it\u2019s ready.</i>"
        )
    else:
        # Full-product RiskReport block (deployer / holders / first-buyer /
        # top-25 / fresh% / CEX% / bundle / cluster / honeypot / liquidity +
        # ~30-factor score + rug/scam determination) — the same RiskReport the
        # /scan formatter renders. Gated upstream: a report with too few known
        # factors to declare a verdict is omitted entirely rather than
        # rendering a composite that contradicts the card header.
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


#: Per-chain tx explorer host (hostname, no scheme/path) for whale-move links.
_TX_EXPLORER: tuple[tuple[str, str], ...] = (
    ("solana", "https://solscan.io/tx/"),
    ("ethereum", "https://etherscan.io/tx/"),
    ("base", "https://basescan.org/tx/"),
    ("arbitrum", "https://arbiscan.io/tx/"),
    ("bsc", "https://bscscan.com/tx/"),
    ("polygon", "https://polygonscan.com/tx/"),
    ("avalanche", "https://snowtrace.io/tx/"),
)


def _tx_url(chain: str, tx_hash: str) -> str:
    """Per-chain transaction explorer URL ("" when unknown)."""
    tx_hash = (tx_hash or "").strip()
    if not tx_hash:
        return ""
    for c, prefix in _TX_EXPLORER:
        if c == (chain or "").lower():
            return f"{prefix}{tx_hash}"
    return ""


def _fmt_move_time(value: Any) -> str:
    """Readable timestamp from a whale move (ISO or epoch; "" when absent)."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.replace(".", "", 1).isdigit():  # epoch seconds/millis
        try:
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            return f"🕐 {datetime.fromtimestamp(ts, tz=UTC).strftime('%b %d, %H:%M UTC')}"
        except (ValueError, OSError):
            return f"🕐 {raw}"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return f"🕐 {dt.strftime('%b %d, %H:%M UTC')}"
    except ValueError:
        return f"🕐 {raw}"


def _whale_move_lines(move: dict[str, Any]) -> list[str] | None:
    """The whale-move lines for the alpha digest, with the ACTUAL signal.

    Whale-signal gate: a whale move with NO wallet address (from/to) on
    either end is DROPPED (``None``) — a whale alert without the wallet is
    worthless noise. Every rendered move carries: wallet route (full,
    copy-able addresses), amount (USD + token), token + chain, a timestamp,
    and a scan/chart/tx source link. Pure — never raises.
    """
    frm = str(move.get("from_address") or "").strip()
    to = str(move.get("to_address") or "").strip()
    if not (frm or to):
        return None
    title = _esc(move.get("title") or "Whale move")
    chain = _esc(move.get("chain") or "solana")
    lines = [f"\u2022 {title} ({chain})"]

    # Amount + token (USD value first, then token symbol).
    usd = _fmt_usd(move.get("value_usd"))
    token = str(move.get("token_symbol") or "").strip()
    bits: list[str] = []
    if usd:
        bits.append(usd)
    if token and token not in ("?", "unknown"):
        bits.append(_esc(token))
    if bits:
        lines.append("   \U0001f4b0 " + " ".join(bits))

    # Wallet route (full addresses — the actual wallet signal).
    if frm and to:
        lines.append(f"   \U0001f45b <code>{_esc(frm)}</code> → <code>{_esc(to)}</code>")
    elif to:
        lines.append(f"   \U0001f45b → <code>{_esc(to)}</code>")
    elif frm:
        lines.append(f"   \U0001f45b <code>{_esc(frm)}</code> →")

    body = _esc(_takeaway(move.get("body") or move.get("summary"), _ALPHA_TAKEAWAY_CHARS))
    if body:
        lines.append(f"   \U0001f4ac {body}")

    addr = str(move.get("token_address") or "")
    links = ""
    if addr:
        lines.append(f"   \U0001f916 /scan <code>{_esc(addr)}</code>")
        links = _item_links(move, addr=addr, chain=str(move.get("chain") or "solana"))
    tx = str(move.get("transaction_hash") or "").strip()
    if tx:
        tx_link = _tx_url(str(move.get("chain") or ""), tx)
        if tx_link:
            tx_bit = f'<a href="{html.escape(tx_link, quote=True)}">Tx</a>'
            links = f"{links} · {tx_bit}" if links else tx_bit
    if links:
        lines.append(f"   {links}")

    ts = _fmt_move_time(move.get("timestamp"))
    if ts:
        lines.append(f"   {ts}")
    return lines


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

    rendered_whales: list[str] = []
    for move in whale_moves[:4]:
        move_lines = _whale_move_lines(move)
        if move_lines:
            rendered_whales.extend(move_lines)
    if rendered_whales:
        lines.append("\u26a1 <b>Whale Moves</b>")
        lines.extend(rendered_whales)
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

    # Empty-window honesty: render the fallback only when NO section actually
    # produced content. A whale list whose only move was dropped (wallet-less)
    # does NOT keep the section header, and a fully-empty digest says so.
    if not rendered_whales and not smart_money and not launches and not kol_trades:
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
