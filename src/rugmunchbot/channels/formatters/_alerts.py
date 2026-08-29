from __future__ import annotations

import html
from contextlib import suppress
from typing import Any

from rugmunchbot.channels.formatters._primitives import (
    NewsPost,
    _clip,
    _esc,
    _now_stamp,
    _pick_image_url,
    _risk_emoji,
    _scan_channel_shill,
    site_news_line,
)
from rugmunchbot.formatters import footer_links, gmgn_url, sep


def _scanner_url(address: str, chain: str) -> str:
    """RugMunch scanner report deep link for a token ("" when no address)."""
    if not address:
        return ""
    return f"https://rugmunch.io/scanner?token={address}&chain={chain or ''}"


def _explorer_url(address: str, chain: str) -> str:
    """Per-wallet explorer link for smart-money wallets ("" when unknown)."""
    if not address:
        return ""
    if (chain or "").lower() == "solana":
        return f"https://solscan.io/account/{address}"
    return ""


def _item_links(item: dict[str, Any], *, addr: str = "", chain: str = "") -> str:
    """One line of actionable links for a curated token item ("" when none).

    Prefers payload-supplied ``scanner_url``/``chart_url``; falls back to
    building the standard scan (rugmunch.io/scanner) and chart (GMGN) deep
    links from the token address + chain. ``explorer_url`` renders only when
    the payload carries one (wallet pages are not token pages). Fail-open.
    """
    address = str(item.get("address") or item.get("token_address") or addr or "")
    chain_name = str(item.get("chain") or chain or "")
    parts: list[str] = []
    scanner = str(item.get("scanner_url") or _scanner_url(address, chain_name)).strip()
    if scanner:
        parts.append(f'<a href="{html.escape(scanner, quote=True)}">🔍 Scan</a>')
    chart = str(item.get("chart_url") or "").strip()
    if not chart and address:
        chart = gmgn_url(address, chain_name)
    if chart:
        parts.append(f'<a href="{html.escape(chart, quote=True)}">📊 Chart</a>')
    explorer = str(item.get("explorer_url") or "").strip()
    if explorer:
        parts.append(f'<a href="{html.escape(explorer, quote=True)}">🧭 Explorer</a>')
    return " · ".join(parts)


_ALERT_VERDICT_REASONS: tuple[tuple[str, str], ...] = (
    ("honeypot", "honeypot — sells can be blocked after buying"),
    ("rugpull", "rugpull indicators — exit-liquidity risk"),
    ("rug_pull", "rugpull indicators — exit-liquidity risk"),
    ("rug_indicators", "rugpull indicators — exit-liquidity risk"),
    ("rug_pattern", "rug-pattern behavior — exit-liquidity risk"),
    ("lp_removed", "LP removed — liquidity can be yanked at any time"),
    ("liquidity_removed", "liquidity removed — holders cannot exit"),
    ("liquidity_low", "low liquidity — price is easy to manipulate"),
    ("low_liquidity", "low liquidity — price is easy to manipulate"),
    ("no_liquidity", "no liquidity — the token cannot be traded"),
    ("volume_fake", "fake volume — activity is wash-traded, not real"),
    ("wash_trad", "wash trading detected — volume is fabricated"),
    ("bundled_launch", "bundled launch — supply is concentrated at the top"),
    ("bundle_detect", "bundled launch — supply is concentrated at the top"),
    ("pump_dump", "pump-and-dump pattern — dump risk after the run"),
    ("malicious_deployer", "malicious deployer — the contract can be changed or rugged"),
    ("high_risk_deployer", "high-risk deployer — dev wallet is flagged"),
    ("suspicious_deployer", "suspicious deployer — dev wallet is flagged"),
    ("dev_high_risk", "high-risk deployer — dev wallet is flagged"),
    ("concentrated_holders", "concentrated holders — top wallets control the supply"),
    ("concentrated_lp", "concentrated LP — shallow, easily manipulated liquidity"),
    ("mintable", "mintable — supply can be inflated after launch"),
    ("sanctioned", "sanctioned entity — compliance and exit risk"),
    ("copycat", "copycat — impersonates a known token"),
    ("active_proxy_owner", "active proxy owner — contract logic can be swapped"),
    ("risk_level=critical", "critical risk verdict from the scanner"),
    ("risk_level=high", "high risk verdict from the scanner"),
)


_ALERT_VERDICT_FALLBACK = "flagged by the RugMunch scanner — verify before touching it"


_SCAN_REASON_BITS: tuple[tuple[str, str], ...] = (
    ("honeypot", "honeypot: sells can be blocked"),
    ("rugpull", "rug indicators: exit liquidity at risk"),
    ("rug_pull", "rug indicators: exit liquidity at risk"),
    ("rug_indicators", "rug indicators: exit liquidity at risk"),
    ("rug_pattern", "rug-pattern behavior"),
    ("lp_removed", "LP removed: holders can't exit"),
    ("liquidity_removed", "LP removed: holders can't exit"),
    ("liquidity_unlocked", "LP unlocked: can be yanked anytime"),
    ("lp_unlocked", "LP unlocked: can be yanked anytime"),
    ("liquidity_low", "thin liquidity: price is easy to manipulate"),
    ("low_liquidity", "thin liquidity: price is easy to manipulate"),
    ("no_liquidity", "no liquidity: token can't be traded"),
    ("volume_fake", "fake volume: wash-traded"),
    ("wash_trad", "fake volume: wash-traded"),
    ("bundled_launch", "bundled launch: supply concentrated"),
    ("bundle_detect", "bundled launch: supply concentrated"),
    ("bundle_suspect", "bundled launch: supply concentrated"),
    ("pump_dump", "pump & dump pattern"),
    ("malicious_deployer", "flagged deployer: dev can rug"),
    ("high_risk_deployer", "flagged deployer: dev can rug"),
    ("suspicious_deployer", "flagged deployer: dev can rug"),
    ("dev_high_risk", "flagged deployer: dev can rug"),
    ("concentrated_holders", "top wallets control the supply"),
    ("concentrated_lp", "concentrated LP: shallow"),
    ("mintable", "mintable: supply can be inflated"),
    ("mint_authority", "mint authority: supply can be inflated"),
    ("fresh_wallet", "fresh-wallet funded"),
    ("deployer_dump", "deployer dumping"),
    ("sanctioned", "sanctioned entity"),
    ("copycat", "copycat of a known token"),
    ("active_proxy_owner", "proxy owner: logic can swap"),
)


def _scan_reason_bits(item: dict[str, Any], *, limit: int = 3) -> list[str]:
    """Human "why it's risky" phrases for a scan card, from flags + security.

    Known flags map to their short reason (most specific first); unknown
    flags stay raw so no signal is dropped. The ``security`` sub-dict adds
    the LP-unlocked signal the flag rail does not carry. Falls back to the
    generic :func:`alert_verdict` when nothing specific is known. Pure —
    never raises.
    """
    bits: list[str] = []
    seen: set[str] = set()
    flags = [str(f).lower() for f in (item.get("risk_flags") or []) if f]
    consumed: set[str] = set()
    for flag in flags:
        for needle, reason in _SCAN_REASON_BITS:
            if needle in flag:
                if reason not in seen:
                    seen.add(reason)
                    bits.append(reason)
                consumed.add(flag)
                break
        if len(bits) >= limit:
            break
    security = item.get("security")
    if isinstance(security, dict) and security.get("lp_locked") is False:
        reason = "LP unlocked: can be yanked anytime"
        if reason not in seen and len(bits) < limit:
            seen.add(reason)
            bits.append(reason)
    for flag in flags:
        if flag in consumed or len(bits) >= limit:
            continue
        if flag not in seen:
            seen.add(flag)
            bits.append(flag)
    if not bits:
        bits.append(alert_verdict(item))
    return bits[:limit]


def alert_verdict(item: dict[str, Any]) -> str:
    """One-line "why it matters" for a scanner alert, derived from known flags.

    First known flag match wins (most specific patterns are listed first);
    unknown flags fall back to an honest generic line. Pure — never raises.
    """
    flags = [str(f).lower() for f in (item.get("risk_flags") or []) if f]
    for flag in flags:
        for needle, reason in _ALERT_VERDICT_REASONS:
            if needle in flag:
                return reason
    risk = str(item.get("risk_level") or "").lower()
    if risk in ("critical", "high"):
        return f"{risk}-risk verdict from the scanner — check before buying"
    return _ALERT_VERDICT_FALLBACK


def format_alerts_post(alerts: list[dict[str, Any]]) -> NewsPost:
    """Urgent risk digest: alert type, condition met vs current, why-it-matters, bot CTA.

    Each flagged token renders: the alert type, the token, the condition that
    was met vs the current scanner state (``condition``), a one-line
    why-it-matters verdict (derived from known risk flags), and actionable
    scan + chart links. Every post ends with a bot CTA so readers know to
    /scan with @RugMunchBot. Carries the first usable image on the returned
    ``NewsPost`` so the scheduler can attach it as a photo (text-only when
    none — fail-open).
    """
    lines = [
        "\U0001f6a8 <b>Risk Alerts</b>",
        "<i>High-risk tokens auto-flagged this window \u2014 /scan with @RugMunchBot before buying.</i>",
        sep(),
        "",
    ]
    if not alerts:
        lines.append("✅ No new high-risk tokens detected in the last window. All clear.")
        lines.extend(["", f"\U0001f550 {_now_stamp()}"])
        lines.append(site_news_line())
        lines.append(sep())
        lines.append(_scan_channel_shill())
        return NewsPost(_clip("\n".join(lines)))
    for item in alerts[:6]:
        symbol = _esc(item.get("symbol") or "???")
        name = _esc(item.get("name") or symbol)
        addr = str(item.get("address") or "")
        chain = _esc(item.get("chain") or "")
        risk = _esc(item.get("risk_level") or "unknown")
        alert_type = _esc(item.get("alert_type") or "scanner")
        lines.append(f"\U0001f6a8 <b>Alert \u2014 {alert_type.title()}</b>")
        lines.append(f"{_risk_emoji(risk)} <b>{name}</b> ({symbol})")
        if addr:
            lines.append(f"<code>{_esc(addr)}</code>")
        condition = str(item.get("condition") or "").strip()
        if not condition:
            score = item.get("risk_score")
            if score is not None:
                with suppress(TypeError, ValueError):
                    condition = f"risk {float(score):.0f}/100 (flagged {risk})"
            if not condition:
                condition = f"flagged {risk}"
        lines.append(f"\u26a1 Condition: {_esc(condition)}")
        lines.append(f"\U0001f4a1 {_esc(alert_verdict(item))}")
        flags = [f for f in item.get("risk_flags") or [] if f]
        if flags:
            lines.append("   \u26a0\ufe0f " + " \u00b7 ".join(_esc(f) for f in flags[:5]))
        if chain:
            lines.append(f"\u26d3\ufe0f {chain}")
        links = _item_links(item, addr=addr, chain=str(item.get("chain") or ""))
        if addr:
            scan_cmd = f"/scan <code>{_esc(addr)}</code>"
            links = f"{links} \u00b7 {scan_cmd}" if links else scan_cmd
        if links:
            lines.append("")
            lines.append(links)
        lines.append("")
    lines.append(sep())
    lines.append(site_news_line())
    lines.append(_scan_channel_shill())
    post = NewsPost(_clip("\n".join(lines)))
    for item in alerts[:6]:
        candidate = _pick_image_url(item.get("image_url"))
        if candidate:
            post.image_url = candidate
            break
    return post


def format_main_escalation(item: dict[str, Any]) -> NewsPost:
    """Solo @cryptorugmuncher card for a reranker-escalated channel item (≥0.7).

    Used by the alerts/alpha jobs when an item clears the main gate: a
    high-signal headline (token or signal name), the condition that was met,
    a one-line why-it-matters, and the actionable scan/chart links. Carries
    the item's image on the returned ``NewsPost`` when one exists (text-only
    otherwise). The item also posts to its native channel digest — this card
    is the additive main-channel surface only.
    """
    name = str(item.get("name") or item.get("title") or item.get("symbol") or "Signal").strip()
    symbol = str(item.get("symbol") or "").strip()
    headline = _esc(f"{name} ({symbol})".strip() if symbol else name)
    addr = str(item.get("address") or item.get("token_address") or "")
    scanner = str(
        item.get("scanner_url") or _scanner_url(addr, str(item.get("chain") or ""))
    ).strip()
    if scanner:
        headline = f'<a href="{html.escape(scanner, quote=True)}">{headline}</a>'
    lines = ["🚨 <b>HIGH SIGNAL</b>", sep(), ""]
    lines.append(headline)
    if addr:
        lines.append(f"<code>{_esc(addr)}</code>")
    condition = str(item.get("condition") or "").strip()
    verdict = str(item.get("verdict") or item.get("summary") or "").strip()
    risk = str(item.get("risk_level") or "").lower()
    if not verdict and (item.get("risk_flags") or risk in ("critical", "high")):
        verdict = alert_verdict(item)
    bits: list[str] = []
    if condition:
        bits.append(f"⚡ <b>Condition met:</b> {_esc(condition)}")
    if verdict:
        bits.append(f"💡 <b>Why it matters:</b> {_esc(verdict)}")
    if bits:
        lines.append("")
        lines.extend(bits)
    chain = _esc(item.get("chain") or "")
    if chain:
        lines.append(f"⛓️ {chain}")
    links = _item_links(item, addr=addr, chain=str(item.get("chain") or ""))
    if links:
        lines.append("")
        lines.append(links)
    lines.extend(["", f"🕐 {_now_stamp()}"])
    lines.append(site_news_line())
    lines.append(footer_links())
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = _pick_image_url(item.get("image_url"))
    return post
