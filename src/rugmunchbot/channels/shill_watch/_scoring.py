"""Shill-campaign scoring, scanner evidence, and alert formatting.

Split out of ``channels/shill_watch.py`` during the de-godding pass. Ranks a
campaign by rerank importance, runs address-token campaigns through the
canonical scanner for a concrete "why this is likely a scam" note, and
formats the HTML alert card. Fail-open everywhere.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.channels.shill_watch._config import (
    SHILL_WINDOW_HOURS,
    logger,
)
from rugmunchbot.channels.shill_watch._detect import ShillCampaign, _esc, _x_status_url

# ── Shill→scanner evidence (2026-09-01) ────────────────────────────────────
# A detected campaign that names a REAL token address is run through the
# canonical scanner (``scan_token_v2`` → legacy ``scan_token`` fallback) so
# the alert can carry a concrete "why this is likely a scam" note — honeypot,
# deployer history, bundled supply, LP risk — instead of a static boilerplate
# sentence. Fail-open: a scanner outage returns ``{}`` and the alert posts on
# the text-only evidence it already has. Only address tokens resolve; a
# ``$TICKER``-only shill has nothing to scan.
SHILL_SCANNER_EVIDENCE_ENABLED = os.getenv("RMI_SHILL_SCANNER_EVIDENCE", "true").lower() in (
    "1",
    "true",
    "yes",
)


async def _score_shill_campaign(campaign: ShillCampaign) -> float:
    """Rerank-importance for one campaign (0.0 on any failure, fail-open).

    Builds a ``NewsArticle`` from the campaign's mention titles/bodies and
    scores it with the same qwen3-reranker lane the news feed uses
    (``channels.importance``). High importance (≥ main gate) signals a
    severe, actively-promoted rug setup → the alert escalates to
    @cryptorugmuncher; below the news floor the campaign is held (never
    spammed). Any reranker outage keeps ``0.0`` so the runner's threshold
    hold applies rather than guessing.
    """
    try:
        from rugmunchbot._vendor.news_ingest.models import NewsArticle
        from rugmunchbot.channels.importance import score_importance

        titles = [str(m.get("title") or "") for m in campaign.mentions]
        body = " ".join(t for t in titles if t)
        article = NewsArticle(
            title=f"Coordinated shill campaign {campaign.token}",
            body=(body or campaign.token)[:1500],
            source_channel=campaign.accounts[0] if campaign.accounts else "shill_watch",
            source_url=_x_status_url(str(campaign.mentions[0].get("url") or ""))
            or "https://rugmunch.io",
            category="scam",
        )
        scored = await score_importance([article])
        if scored:
            campaign.importance = round(scored[0].importance, 4)
            return campaign.importance
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_watch:score_failed err=%s", str(exc)[:120])
    campaign.importance = 0.0
    return 0.0


# ── Shill→scanner evidence (2026-09-01) ────────────────────────────────────
# A detected campaign that names a REAL token address is run through the
# canonical scanner (``scan_token_v2`` → legacy ``scan_token`` fallback) so
# the alert can carry a concrete "why this is likely a scam" note — honeypot,
# deployer history, bundled supply, LP risk — instead of a static boilerplate
# sentence. Fail-open: a scanner outage returns ``{}`` and the alert posts on
# the text-only evidence it already has. Only address tokens resolve; a
# ``$TICKER``-only shill has nothing to scan.


def _looks_like_address(token: str) -> bool:
    """True when ``token`` is a chain address (0x hex / Solana base58 mint).

    Ticker-only shills (``$PEPE``) cannot be scanned — there is no address
    to resolve. Solana mints are base58 (~32-44 chars); EVM are 0x + 40 hex.
    """
    t = str(token or "").strip()
    if not t:
        return False
    if t.startswith("0x"):
        body = t[2:]
        return len(t) >= 34 and all(c in "0123456789abcdefABCDEF" for c in body)
    return 32 <= len(t) <= 44


def _pct(value: object) -> float | None:
    """Coerce a 0..100 percentage to float; None when absent/not a number."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if 0 <= f <= 100 else None


def _evidence_bullets(data: dict[str, Any]) -> list[str]:
    """Build a short, defensible "why this is likely a scam" bullet list.

    Reads BOTH the v2 payload (``risk_level``/``flags``/``evidence``) and the
    legacy scan dict (``honeypot``/``buy_tax``/``bundle_detected``/``lp_locked``
    ...). Only renders a bullet when the scanner returned an actual value that
    crosses a clear red line — never a fabricated or guessed number. Returns
    ``[]`` when nothing defensible is present.
    """
    bullets: list[str] = []

    # ── v2 top-level verdict ──
    risk_level = str(data.get("risk_level") or "").upper()
    safety = data.get("safety_score")
    if risk_level in ("CRITICAL", "HIGH"):
        if isinstance(safety, (int, float)):
            bullets.append(f"Scanner verdict: {risk_level} risk (safety {float(safety):.0f}/100)")
        else:
            bullets.append(f"Scanner verdict: {risk_level} risk")
    flags = data.get("flags") or data.get("risk_flags") or []
    if isinstance(flags, list) and flags:
        bullets.append(f"Scanner flags: {', '.join(str(f)[:60] for f in flags[:4])}")

    evidence = data.get("evidence")
    if isinstance(evidence, dict):
        # Honeypot / security checks.
        sec = evidence.get("security")
        if isinstance(sec, dict):
            try:
                for check in sec.get("checks", {}).values():
                    if isinstance(check, dict) and str(check.get("status", "")).lower() in (
                        "honeypot",
                        "buy_tax",
                        "sell_tax",
                        "can_take_back_ownership",
                        "owner_change_balance",
                    ):
                        bullets.append(
                            f"On-chain check {check.get('status', '?')}: {check.get('details', 'flagged')[:80]}"
                        )
            except Exception:  # defensive — no bullet on a malformed check
                logger.debug("shill_watch:security_checks_parse_failed", exc_info=True)
            rug = sec.get("rug_patterns")
            if isinstance(rug, dict):
                matches = rug.get("match_count") or len(rug.get("patterns") or [])
                if matches:
                    bullets.append(f"Matched {matches} known rug-pull signature(s)")
        # Liquidity / LP risk.
        liq = evidence.get("liquidity")
        if isinstance(liq, dict):
            locked = _pct(liq.get("locked_pct"))
            if locked is not None and locked < 20:
                bullets.append(f"LP only {locked:.0f}% locked — early-exit/removal risk")
        # Holder concentration (bundled supply signal).
        holders = evidence.get("holders")
        if isinstance(holders, dict):
            top10 = _pct(holders.get("top_10_pct"))
            if top10 is not None and top10 >= 60:
                bullets.append(f"Top 10 holders control {top10:.0f}% of supply")
        # Fresh / mixer-funded supply.
        funding = evidence.get("funding_analysis")
        if isinstance(funding, dict):
            fresh = _pct(funding.get("fresh_wallet_pct"))
            if fresh is not None and fresh >= 40:
                bullets.append(f"{fresh:.0f}% of supply held by fresh wallets")
            mixer = _pct(funding.get("mixer_funded_pct"))
            if mixer is not None and mixer >= 20:
                bullets.append(f"{mixer:.0f}% of supply mixer-funded")
        # Deployer history.
        dep = evidence.get("deployer")
        if isinstance(dep, dict):
            label = str(dep.get("risk_label") or "").upper()
            total = dep.get("total_deployments")
            rug = dep.get("rug_count")
            if label in ("REPEAT_RUG", "FARM", "PROLIFIC", "CRITICAL", "HIGH"):
                bullets.append(f"Deployer history: {label}")
            elif (
                isinstance(total, (int, float))
                and float(total) >= 5
                and isinstance(rug, (int, float))
                and float(rug) > 0
            ):
                bullets.append(f"Deployer made {total} token(s), {rug} rug/dump")

    # ── Legacy scanner fields ──
    if data.get("bundle_detected"):
        bullets.append("Bundled launch detected (same-block buy cluster)")
    hp = data.get("honeypot")
    if hp is True or str(hp).lower() in ("true", "yes", "detected"):
        bullets.append("Honeypot detected — cannot sell after buy")
    buy_tax = _pct(data.get("buy_tax"))
    sell_tax = _pct(data.get("sell_tax"))
    if sell_tax is not None and sell_tax >= 20:
        bullets.append(f"Sell tax {sell_tax:.0f}% — exit is taxed/dissuaded")
    if buy_tax is not None and buy_tax >= 20:
        bullets.append(f"Buy tax {buy_tax:.0f}% — entry is taxed")
    if data.get("mintable") and not data.get("owner_renounced"):
        bullets.append("Supply is mintable and ownership not renounced")
    lp_locked = data.get("lp_locked")
    if lp_locked is False or str(lp_locked).lower() in ("no", "false", "unlocked"):
        bullets.append("LP not locked — liquidity can be pulled at any time")
    fresh = _pct(data.get("fresh_wallet_pct"))
    if fresh is not None and fresh >= 40:
        bullets.append(f"{fresh:.0f}% of supply in fresh wallets")

    return bullets


async def build_scanner_evidence(token: str, chain: str) -> dict[str, Any]:
    """Run ``token`` through the scanner; return a compact evidence dict.

    Returns ``{"scanned", "risk_level", "safety_score", "bullets", "note"}``
    or ``{}`` (no address / scanner unavailable / no defensible finding).
    Fail-open by contract — a scanner outage never blocks the campaign alert.
    """
    if not SHILL_SCANNER_EVIDENCE_ENABLED or not _looks_like_address(token):
        return {}
    try:
        from rugmunchbot.scanner import scan_token, scan_token_v2

        data = await scan_token_v2(token, chain)
        if data is None:
            data = await scan_token(token, chain)
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_watch:scanner_evidence_failed err=%s", str(exc)[:120])
        return {}
    if not isinstance(data, dict):
        return {}
    bullets = _evidence_bullets(data)
    risk_level = str(data.get("risk_level") or "").upper()
    if not bullets and risk_level not in ("HIGH", "CRITICAL"):
        # No defensible finding — don't attach a fabricated scare note.
        return {}
    note = " ".join(bullets[:4]) if bullets else f"Scanner verdict: {risk_level} risk."
    safety = data.get("safety_score")
    return {
        "scanned": True,
        "risk_level": risk_level or "unknown",
        "safety_score": round(float(safety), 1) if isinstance(safety, (int, float)) else None,
        "bullets": bullets,
        "note": note,
    }


def format_shill_alert(campaign: ShillCampaign) -> str:
    """HTML alert card for one campaign (Telegram parse_mode=html).

    2026-08-30: the card now explains WHAT is shilled, WHO is behind it, and
    WHY it is likely a scam (coordinated push = pre-arranged rug setup), and
    attaches the screenshot evidence (image path + extracted text) so the
    alert carries proof. ``campaign.importance`` (when scored) is rendered as
    a severity hint.
    """
    token = str(campaign.token)
    if campaign.chain == "ticker":
        head = f"🚨 <b>Coordinated shill: {token}</b>"
    else:
        short = token[:8] + "…" + token[-6:] if len(token) > 16 else token
        head = f"🚨 <b>Coordinated shill: <code>{short}</code></b> ({campaign.chain})"
    lines = [
        head,
        "",
        f"<b>What:</b> {token} pushed by <b>{len(campaign.accounts)} distinct accounts</b> in the last {SHILL_WINDOW_HOURS}h.",
        f"<b>Who:</b> {_esc(campaign.accounts[0])}"
        + (f" + {len(campaign.accounts) - 1} more" if len(campaign.accounts) > 1 else ""),
        "",
    ]
    for channel in campaign.accounts[:6]:
        lines.append(f"  • {_esc(channel)}")
    if len(campaign.accounts) > 6:
        lines.append(f"  …and {len(campaign.accounts) - 6} more")
    lines.extend(
        [
            "",
            "<b>Why this is likely a scam:</b> the same token promoted by many "
            "independent accounts inside one short window is the signature of an "
            "organised pump — the 'promoters' are the exit liquidity. Historically "
            "these campaigns precede a dump the moment the bag-holders arrive.",
        ]
    )
    # 2026-09-01 scanner evidence: when the campaign names a real token address
    # the canonical scanner ran it (build_scanner_evidence) — surface the
    # concrete on-chain findings (honeypot, deployer history, bundled supply,
    # LP risk) instead of relying on the coordination boilerplate alone.
    scanners = campaign.scanner_evidence or {}
    sc_bullets = scanners.get("bullets") or []
    if sc_bullets:
        lines.append("")
        lines.append("On-chain evidence (scanned):")
        for b in sc_bullets[:5]:
            lines.append(f"  • {_esc(b)}")
        if scanners.get("note") and len(sc_bullets) > 5:
            lines.append(f"  … {_esc(scanners['note'][:160])}")
    sample = campaign.mentions[:2]
    if sample:
        lines.append("")
        lines.append("Earliest pushes:")
        for m in sample:
            title = str(m.get("title") or "")[:90]
            url = str(m.get("url") or "")
            if url:
                lines.append(f'  → <a href="{_esc(url)}">{_esc(title)}</a>')
            else:
                lines.append(f"  → {_esc(title)}")
    if campaign.screenshots:
        lines.append("")
        lines.append("Evidence (screenshot capture):")
        for shot in campaign.screenshots[:3]:
            sx = _x_status_url(str(shot.get("url") or "")) or str(shot.get("url") or "")
            txt = str(shot.get("text") or "")[:140].replace("\n", " ")
            src = str(shot.get("account") or shot.get("image_path") or "")
            if txt:
                lines.append(f"  • {_esc(src)}: {_esc(txt)}")
            if sx:
                lines.append(f'    <a href="{_esc(sx)}">{_esc(sx)}</a>')
    lines.append("")
    lines.append("Coordinated shill = high rug-pump risk. Verify before ape: /scan @rugmunchbot")
    return "\n".join(lines)
