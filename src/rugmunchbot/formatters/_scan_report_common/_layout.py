"""Canonical scan-report layout — key metrics table, verdicts, watch line."""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.formatters._scan_report_common._fields import (
    _bundle_wallet_count,
    _cex_funded_pct,
    _fake_volume_pct,
    _float_or_none,
    _freeze_auth,
    _fresh_wallet_pct,
    _holders_count,
    _lp_lock_pct,
    _mev_note,
    _mint_auth,
    _tax_pct_str,
    _token_2022_flag,
    _volume_up_price_flat_churn,
)
from rugmunchbot.formatters._scan_report_common._text import _short_addr
from rugmunchbot.formatters._sentinel_helpers import _fmt_usd_amount
from rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)


_THIN_SEP = "\u2500" * 24


_RISK_HIGH = 70.0


_RISK_MID = 40.0


def _risk_level_word(score: float | None) -> str:
    """Plain risk word (HIGH / MEDIUM / LOW) for a 0-100 score."""
    if score is None:
        return "UNKNOWN"
    if score >= _RISK_HIGH:
        return "HIGH"
    if score >= _RISK_MID:
        return "MEDIUM"
    return "LOW"


def _risk_emoji(score: float | None) -> str:
    """Risk color emoji for a 0-100 score (unknown → ⚪)."""
    if score is None:
        return "\u26aa"  # ⚪
    if score >= _RISK_HIGH:
        return "\U0001f534"  # 🔴
    if score >= _RISK_MID:
        return "\U0001f7e1"  # 🟠
    return "\U0001f7e2"  # 🟢


def _fmt_bool_emoji(value: bool | None, *, danger_on: bool = True) -> str:
    """Emoji for a boolean signal: 🔴 when the danger state is on, else 🟢."""
    if value is None:
        return "\u26aa"  # ⚪ unknown
    return "\U0001f534" if value == danger_on else "\U0001f7e2"


def _fmt_num(value: Any, *, suffix: str = "") -> str:
    """Format a number (thousands separators) plus optional suffix, N/A-safe."""
    try:
        return f"{float(value):,.0f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_market_value(value: Any) -> str:
    """USD string for the Market Data block — "—" when the value is absent.

    Keep-it-honest rule: a provider that did not report a field renders as
    "—", while a genuine 0 (delisted pair, no liquidity) renders as ``$0``.
    """
    if value is None:
        return "\u2014"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "\u2014"
    if v == 0:
        return "$0"
    return _fmt_usd_amount(v)


def _bundle_verdict_row(r: dict[str, Any]) -> str | None:
    """Deep-scan Bundle row — a clear one-line verdict, honest when no data.

    - 🔴 ``Bundle detected: N wallets`` (engine flagged a coordinated launch)
    - 🟢 ``No bundle detected`` (engine scanned the launch window clean)
    - ⚪ ``—`` (engine had no data — never claims "no bundle")
    Returns ``None`` only when the scan carries no bundle payload at all.
    """
    ba = r.get("bundle_analysis")
    if not isinstance(ba, dict):
        return None
    if ba.get("is_bundled"):
        n = _bundle_wallet_count(ba)
        conf = _float_or_none(ba.get("confidence"))
        if conf is None:
            # Engine exposes cluster subscores, not a single confidence --
            # derive one from the strongest coordination signal.
            subscores = [
                _float_or_none(ba.get(k))
                for k in ("launch_cluster_score", "common_funder_score", "similar_amount_score")
            ]
            present = [v for v in subscores if v is not None]
            conf = max(present) if present else None
        risk = html.escape(str(ba.get("risk_label") or "")).upper()
        if not risk and conf is not None:
            risk = "HIGH" if conf >= 70 else "ELEVATED" if conf >= 40 else "LOW"
        # Enrich with cluster subscore detail when available
        details = []
        for k, label in (
            ("launch_cluster_score", "launch"),
            ("common_funder_score", "funder"),
            ("similar_amount_score", "amount"),
        ):
            v = _float_or_none(ba.get(k))
            if v is not None and v >= 50:
                details.append(f"{label} {v:.0f}")
        dfp = _float_or_none(ba.get("deployer_funded_pct"))
        if dfp is not None and dfp >= 30:
            details.append(f"deployer-funded {dfp:.0f}%")
        conf_str = f"{conf:.0f}% conf" if conf is not None else "unknown conf"
        count = f" <b>{n}</b> wallets" if n is not None else ""
        detail_str = f" ({', '.join(details)})" if details else ""
        return f"\U0001f534 Bundle detected:{count} \u00b7 {conf_str} \u00b7 {risk}{detail_str}"
    if str(ba.get("result") or "").lower() == "scanned":
        return "\U0001f7e2 No bundle detected"
    return "\u2014"


def _distribution_verdict(r: dict[str, Any]) -> tuple[str, str] | None:
    """One-line holder-distribution verdict (emoji, word), or None when no data.

    Thresholds mirror the canonical holder_clusters concentration bands
    (top-10 ≥ 50% concentrated / ≥ 30% moderate) and the SENTINEL HHI bands
    (≥ 3000 concentrated / ≥ 1500 moderate). Never claims a verdict when the
    scan carries no concentration data.
    """
    ha = r.get("holder_analysis") if isinstance(r.get("holder_analysis"), dict) else {}
    top10 = _float_or_none(ha.get("top_10_pct")) or _float_or_none(ha.get("top10_pct"))
    if top10 is None:
        ba = r.get("bundle_analysis")
        if isinstance(ba, dict):
            top10 = _float_or_none(ba.get("top10_holder_pct"))
    if top10 is not None:
        if top10 >= 50:
            return ("\U0001f534", "CONCENTRATED")
        if top10 >= 30:
            return ("\U0001f7e1", "MODERATE")
        return ("\U0001f7e2", "HEALTHY")
    hhi = _float_or_none(ha.get("hhi")) if isinstance(ha, dict) else None
    if hhi is not None:
        if hhi >= 3000:
            return ("\U0001f534", "CONCENTRATED")
        if hhi >= 1500:
            return ("\U0001f7e1", "MODERATE")
        return ("\U0001f7e2", "HEALTHY")
    return None


def _holder_distribution_rows(r: dict[str, Any]) -> list[str]:
    """Deep-scan-only Holder Distribution block (empty when no data).

    Surfaces top-10/top-20 supply concentration, sniper/insider wallet
    counts (GMGN holder analysis), and the exchange-funded share of the
    analyzed holders, topped by a one-line distribution verdict. Honest-data
    rule: every value renders only when a provider reported it.
    """
    rows: list[str] = []
    ha = r.get("holder_analysis")
    if not isinstance(ha, dict):
        ha = {}
    top10 = _float_or_none(ha.get("top_10_pct"))
    top20 = _float_or_none(ha.get("top_20_pct"))
    if top10 is not None:
        row = f"  \U0001f40b Top-10 hold <b>{top10:.1f}%</b>"
        if top20 is not None:
            row += f" \u00b7 Top-20 <b>{top20:.1f}%</b>"
        rows.append(row)
    elif top20 is not None:
        rows.append(f"  \U0001f40b Top-20 hold <b>{top20:.1f}%</b>")
    cex = _cex_funded_pct(r)
    if cex is not None:
        rows.append(f"  \U0001f3e6 Exchange-funded <b>{cex:.1f}%</b> of analyzed holders")
    gmgn_holders = (r.get("gmgn") or {}).get("holders") if isinstance(r.get("gmgn"), dict) else None
    if isinstance(gmgn_holders, dict) and (
        gmgn_holders.get("sniper_count") or gmgn_holders.get("insider_count")
    ):
        parts = []
        if gmgn_holders.get("sniper_count"):
            parts.append(f"\U0001f3af <b>{int(gmgn_holders['sniper_count'])}</b> snipers")
        if gmgn_holders.get("insider_count"):
            parts.append(f"\U0001f575\ufe0f <b>{int(gmgn_holders['insider_count'])}</b> insiders")
        rows.append("  " + " \u00b7 ".join(parts))
    verdict = _distribution_verdict(r)
    if verdict:
        rows.append(f"  {verdict[0]} Distribution verdict: <b>{verdict[1]}</b>")
    return rows


def _fake_volume_verdict_text(r: dict[str, Any]) -> str:
    """Dedicated 📉 Fake Volume verdict value for /scan + /deepscan.

    Three honest states (mirrors ``_bundle_verdict_row``):
      - 🔴 <b>Likely</b> (wash-trading detected: N self-deals · volume 10x holders)
      - 🟢 Organic volume patterns
      - — (no volume-authenticity data — never claims "clean" without a check)

    Evidence order: the own volume-authenticity engine's buyer/seller-overlap
    self-deal count (same wallet buys AND sells), its volume-to-holder churn
    ratio (high volume over a tiny holder base), the aggregate authentic
    score, then the market-side volume-up/price-flat churn note. All detail
    strings are HTML-escaped.
    """
    wash = r.get("wash_trading")
    if not isinstance(wash, dict) or not wash:
        return "\u2014"
    verdict = str(wash.get("fake_volume_verdict") or "").strip()
    if verdict not in ("likely", "organic"):
        # Legacy payloads may only carry the percentage — fall back on it.
        pct = _fake_volume_pct(r)
        if pct is None:
            return "\u2014"
        verdict = "likely" if pct > 10 else "organic"

    detail = str(wash.get("fake_volume_detail") or "").strip()
    parts = [p.strip() for p in detail.split("\u00b7") if p.strip()]

    if verdict == "likely":
        churn = _volume_up_price_flat_churn(r)
        if churn:
            parts.append(churn)
        if not parts:
            pct = _fake_volume_pct(r)
            parts.append(f"{pct:.0f}% fabricated" if pct is not None else "wash-trading signals")
        inner = " \u00b7 ".join(html.escape(p) for p in parts)
        return f"\U0001f534 <b>Likely</b> (wash-trading detected: {inner})"
    return "\U0001f7e2 Organic volume patterns"


def _key_metrics_rows(r: dict[str, Any], *, deep: bool = False) -> list[str]:
    """Tight bold key-metrics table shared by /scan and the deep scans.

    Rows: risk score, liquidity, holders, mint auth, freeze auth and
    buy/sell tax (always); LP lock %, bundle verdict and snipers/insiders
    (deep only). Values are bold and labels fixed-width so the block reads
    as a table on narrow Telegram screens. Emits the section heading + thin
    separator; returns an empty list only for an empty report dict.
    """
    risk_raw = r.get("risk_score")
    try:
        risk = float(risk_raw) if risk_raw is not None else None
    except (TypeError, ValueError):
        risk = None

    rows: list[str] = [
        "  Risk Score     " + f"{_risk_emoji(risk)} <b>{risk:.0f}/100</b> {_risk_level_word(risk)}"
        if risk is not None
        else f"  Risk Score     {_risk_emoji(None)} <b>N/A</b>",
    ]
    liquidity = r.get("liquidity_usd") or r.get("liquidity")
    rows.append(f"  Liquidity      <b>{_fmt_market_value(liquidity)}</b>")
    holders = _holders_count(r)
    rows.append(
        f"  Holders        <b>{holders:,}</b>"
        if holders is not None
        else "  Holders        <b>N/A</b>"
    )

    mint = _mint_auth(r)
    rows.append(
        f"  Mint Auth      {_fmt_bool_emoji(mint)} <b>{'ACTIVE' if mint else 'renounced' if mint is not None else 'N/A'}</b>"
    )
    freeze = _freeze_auth(r)
    rows.append(
        f"  Freeze Auth    {_fmt_bool_emoji(freeze)} <b>{'ACTIVE' if freeze else 'renounced' if freeze is not None else 'N/A'}</b>"
    )
    buy_tax = _tax_pct_str(r.get("buy_tax"))
    sell_tax = _tax_pct_str(r.get("sell_tax"))
    rows.append(f"  Buy/Sell Tax   <b>{buy_tax}</b> / <b>{sell_tax}</b>")

    # Dedicated Fake Volume verdict (own volume-authenticity engine) — a
    # clear one-line verdict for BOTH /scan and /deepscan, not buried in a
    # sub-metric: 🔴 Likely (wash-trading detected: …) / 🟢 Organic / — .
    rows.append(f"  \U0001f4c9 Fake Volume   {_fake_volume_verdict_text(r)}")

    if deep:
        lp = _lp_lock_pct(r)
        rows.append(
            f"  LP Lock        {_risk_emoji(lp)} <b>{lp:.0f}%</b>"
            if lp is not None
            else "  LP Lock        <b>N/A</b>"
        )
        bundle_row = _bundle_verdict_row(r)
        if bundle_row is not None:
            rows.append(f"  Bundle         {bundle_row}")
        else:
            # Strategy v2 completeness bar: a missing bundle payload is
            # shown honestly — silence would read as "scanned, clean".
            rows.append("  Bundle         ⚪ <b>N/A</b>")
        cex = _cex_funded_pct(r)
        rows.append(
            f"  CEX-Funded     \U0001f3e6 <b>{cex:.1f}%</b>"
            if cex is not None
            else "  CEX-Funded     <b>\u2014</b>"
        )
        fresh = _fresh_wallet_pct(r)
        rows.append(
            f"  Fresh Wallets  \U0001f195 <b>{fresh:.1f}%</b>"
            if fresh is not None
            else "  Fresh Wallets  <b>\u2014</b>"
        )
        gini = (r.get("holder_analysis") or {}).get("gini_coefficient")
        if gini is not None:
            try:
                gini_val = float(gini)
                gini_label = (
                    "EXTREME" if gini_val >= 0.8 else "HIGH" if gini_val >= 0.6 else "MODERATE"
                )
                rows.append(f"  Gini Coeff     \U0001f4ca <b>{gini_val:.2f}</b> ({gini_label})")
            except (TypeError, ValueError):
                pass
        gmgn_holders = (
            (r.get("gmgn") or {}).get("holders") if isinstance(r.get("gmgn"), dict) else None
        )
        if isinstance(gmgn_holders, dict) and (
            gmgn_holders.get("sniper_count") or gmgn_holders.get("insider_count")
        ):
            parts = []
            if gmgn_holders.get("sniper_count"):
                parts.append(f"\U0001f3af snipers <b>{int(gmgn_holders['sniper_count'])}</b>")
            if gmgn_holders.get("insider_count"):
                parts.append(
                    f"\U0001f575\ufe0f insiders <b>{int(gmgn_holders['insider_count'])}</b>"
                )
            sep_dot = " \u00b7 "
            rows.append(f"  Snipers        {sep_dot.join(parts)}")

    return ["", "\U0001f4ca <b>Key Metrics</b>", _THIN_SEP, *rows]


def _launch_intel_rows(r: dict[str, Any]) -> list[str]:
    """Deep-scan-only "Launch & Trading Intel" rows (empty when no data).

    Surfaces what a trader actually needs beyond the base report: bundle
    launch details (confidence, deployer-funded, first-time buyers), sniper
    / insider counts, Token-2022 program flags and MEV notes. Every row is
    data-driven — nothing renders when the scan carries no such data.
    """
    rows: list[str] = []
    ba = r.get("bundle_analysis")
    if isinstance(ba, dict) and ba.get("is_bundled"):
        conf = _fmt_num(ba.get("confidence"), suffix="%")
        n = _bundle_wallet_count(ba)
        count = f", <b>{n}</b> launch wallets" if n is not None else ""
        rows.append(
            f"  \U0001f4e6 Bundle launch: <b>{conf}</b> confidence{count}, "
            f"{html.escape(str(ba.get('risk_label') or 'unknown')).upper()} risk"
        )
        if ba.get("deployer_funded_pct") is not None:
            rows.append(
                f"  \U0001f468\u200d\U0001f4bb Deployer-funded: <b>{_fmt_num(ba.get('deployer_funded_pct'), suffix='%')}</b>"
            )
        if ba.get("first_time_buyer_pct") is not None:
            rows.append(
                f"  \U0001f195 First-time buyers: <b>{_fmt_num(ba.get('first_time_buyer_pct'), suffix='%')}</b>"
            )
    gmgn_holders = (r.get("gmgn") or {}).get("holders") if isinstance(r.get("gmgn"), dict) else None
    if isinstance(gmgn_holders, dict) and (
        gmgn_holders.get("sniper_count") or gmgn_holders.get("insider_count")
    ):
        parts = []
        if gmgn_holders.get("sniper_count"):
            parts.append(f"\U0001f3af <b>{int(gmgn_holders['sniper_count'])}</b> snipers")
        if gmgn_holders.get("insider_count"):
            parts.append(f"\U0001f575\ufe0f <b>{int(gmgn_holders['insider_count'])}</b> insiders")
        rows.append("  " + " \u00b7 ".join(parts))
    token_2022 = _token_2022_flag(r)
    if token_2022:
        rows.append(f"  \U0001f9ea {token_2022}")
    mev = _mev_note(r)
    if mev:
        rows.append(f"  \U0001f50d {mev}")
    return rows


def _what_to_watch_line(r: dict[str, Any]) -> str:
    """Short actionable "what to watch" line (HTML-safe, single line).

    Picks the single most important behavior to watch from the scan's top
    finding. Priority mirrors the verdict sentence: honeypot > mint >
    freeze/blacklist > ownership > unverified > LP > bundle > fake volume >
    fresh wallets; otherwise a generic caution line.
    """
    if r.get("honeypot") is True:
        return "Don't buy \u2014 sells may be blocked. Verify with a small test sell first."
    if _mint_auth(r) is True:
        return "Supply can be inflated anytime \u2014 watch for sudden dilution dumps."
    if _freeze_auth(r) is True:
        return "Owner can freeze/blacklist wallets \u2014 one bad txn locks you out."
    if r.get("owner_renounced") is False:
        return "Deployer still controls the contract \u2014 watch for ownership abuse."
    if r.get("contract_verified") is False:
        return "Contract is unverified \u2014 treat the code as a black box."
    lp = _lp_lock_pct(r)
    if lp is not None and lp < 50:
        return f"LP only {lp:.0f}% locked \u2014 a pull drains liquidity fast."
    fa = r.get("flow_analysis")
    if isinstance(fa, dict) and fa.get("concentrated_flow"):
        share = fa.get("top_mover_share_pct")
        share_s = f" ({share:.0f}% one wallet)" if isinstance(share, (int, float)) else ""
        return f"Flow concentrated{share_s} \u2014 one wallet dominates recent transfers; accumulation-or-dump setup."
    ba = r.get("bundle_analysis")
    if isinstance(ba, dict) and ba.get("is_bundled"):
        return "Bundled launch \u2014 volume/concentration may be fabricated; don't trust momentum."
    fake_vol = _fake_volume_pct(r)
    if fake_vol is not None and fake_vol > 10:
        return "Fake volume suspected \u2014 price action may not be real."
    fresh = _fresh_wallet_pct(r)
    if fresh is not None and fresh > 50:
        return "Fresh wallets dominate \u2014 likely a sniped / coordinated launch."
    return (
        "Standard caution \u2014 size in small and watch sell pressure on the first green candle."
    )


# ── 🛒 First Buyers (own-indexer engine, 2026-08-25 quality push) ──
# Wallet-age bucket labels for the per-buyer rows (buckets come from the
# backend's analyze_first_buyers_rows: wallet age AT its first buy).
_FB_BUCKET_LABELS = {
    "fresh": "\U0001f195 fresh",  # 🆕 born <24h before its first buy
    "new": "\U0001f331 <7d",  # 🌱 born <7d before its first buy
    "established": "\U0001f3e6 est",  # 🏦 older than 7d at first buy
    "unknown": "\u2753",  # ❓ no birth data indexed
}


def _fmt_age_compact(seconds: Any) -> str:
    """Human compact duration for first-buyer ages (45s / 12m / 3h / 2d)."""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return ""
    if s >= 86400:
        return f"{s / 86400:.0f}d"
    if s >= 3600:
        return f"{s / 3600:.0f}h"
    if s >= 60:
        return f"{s / 60:.0f}m"
    return f"{s:.0f}s"


def _first_buyers_rows(r: dict[str, Any], *, deep: bool = False) -> list[str]:
    """🛒 First Buyers block rows — earliest buyers from OUR indexer.

    basic (``/scan``): one aggregates line over the top-25 earliest buyers.
    deep (``/scandeep``): top-25 buyer rows + the aggregates over the full
    deep cohort (250 wallets on the deep endpoint / scanner engine — the
    backend's ``first_buyers.limit`` is shown explicitly when present).
    Honest-data rule: renders nothing when the scan carries no
    ``first_buyers`` payload (older backend, ClickHouse unreachable, or a
    token with no indexed buys yet) — never fabricates buyer data.
    """
    fb = r.get("first_buyers")
    if not isinstance(fb, dict):
        return []
    buyers = fb.get("buyers")
    if not isinstance(buyers, list) or not buyers:
        return []
    agg = fb.get("aggregates") if isinstance(fb.get("aggregates"), dict) else {}
    cohort_limit = fb.get("limit")
    if not isinstance(cohort_limit, int):
        cohort_limit = len(buyers)

    rows: list[str] = []
    if deep:
        rank = 0
        for b in buyers[:25]:
            if not isinstance(b, dict):
                continue
            wallet = str(b.get("wallet") or "")
            if not wallet:
                continue
            rank += 1
            bits = [_FB_BUCKET_LABELS.get(str(b.get("wallet_age_bucket")), "\u2753")]
            age_s = _float_or_none(b.get("age_at_buy_s"))
            if age_s is not None and age_s > 0:
                bits.append(f"bought {_fmt_age_compact(age_s)} old")
            if b.get("bundled"):
                bits.append("\U0001f517 bundle")
            joined = " \u00b7 ".join(bits)
            rows.append(f"  {rank}. <code>{_short_addr(wallet)}</code> {joined}")

    # Aggregates line — both modes (basic renders ONLY this line).
    parts = [f"<b>{len(buyers)}</b> tracked"]
    if deep:
        parts.append(f"first-{cohort_limit} cohort")
    else:
        parts.append(f"first-{cohort_limit}")
    fresh = _float_or_none(agg.get("fresh_wallet_pct"))
    if fresh is not None:
        parts.append(f"\U0001f195 {fresh:.0f}% fresh wallets")
    established = _float_or_none(agg.get("established_pct"))
    if established is not None:
        parts.append(f"\U0001f3e6 {established:.0f}% established")
    bundled = _float_or_none(agg.get("bundled_pct"))
    if bundled is not None:
        parts.append(f"\U0001f517 {bundled:.0f}% bundled")
    median = _float_or_none(agg.get("median_latency_s"))
    if median is not None and median > 0:
        parts.append(f"\u23f1 entry wave +{_fmt_age_compact(median)}")
    rows.append("  " + " \u00b7 ".join(parts))
    return rows
