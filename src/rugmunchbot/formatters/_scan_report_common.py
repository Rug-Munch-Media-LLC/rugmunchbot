"""Shared pure text helpers for the scan-report / SENTINEL formatters.

Split out of ``formatters/_scan_report.py`` during the Batch 14
de-godding pass: generic string utils used by every section module.
No bot or keyboard dependencies.

Also hosts the canonical scan-report LAYOUT helpers (Batch 2026-08-16):
every scan report — regular ``/scan``, ``/scandeep``, ``/sentinelscan`` and
the ML ``/deepscan`` — shares the same 4-part skeleton:

  1. one-line VERDICT up top
  2. key metrics as a tight bold table
  3. evidence bullets
  4. a short "what to watch" line

``_key_metrics_rows`` and ``_what_to_watch_line`` render parts 2 and 4 from
a legacy-scan report dict so the regular assembler and the advanced
formatters stay on ONE layout. All data-derived strings are HTML-escaped
here (user/token content must never inject markup into a Telegram-HTML
payload).
"""

from __future__ import annotations

import html
import re
from typing import Any

from app.domains.telegram.rugmunchbot.formatters._sentinel_helpers import _fmt_usd_amount
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "_bundle_verdict_row",
    "_bundle_wallet_count",
    "_cex_funded_pct",
    "_clip",
    "_collapse_blank_lines",
    "_distribution_verdict",
    "_fake_volume_verdict_text",
    "_float_or_none",
    "_fmt_market_value",
    "_format_scam_signals_section",
    "_holder_distribution_rows",
    "_key_metrics_rows",
    "_launch_intel_rows",
    "_lp_lock_pct",
    "_mint_auth",
    "_scam_signal_rows",
    "_short_addr",
    "_what_to_watch_line",
]


def _clip(text: object, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars, adding a leading-ellipsis mark
    (``…``) ONLY when content was actually cut. Avoids the mid-tag / mid-word
    garbage that a bare ``[:n]`` can leave in a Telegram-HTML payload."""
    s = str(text or "").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "\u2026"


def _collapse_blank_lines(text: str) -> str:
    """Collapse runs of 3+ newlines to 2 (one blank line).

    Guard for section interleaving: a red-alert banner followed by the next
    section header used to render a double blank line. Pure string pass, so
    every section keeps its own leading blank without stacking extras.
    """
    return re.sub(r"\n{3,}", "\n\n", text)


def _short_addr(addr: str) -> str:
    """Compact ``0xAb12...34cd`` form for long wallet addresses."""
    addr = str(addr)
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


# ── Canonical scan-report layout (regular /scan + advanced deep scans) ─────
# Separator kept local so this module stays import-free (matches
# ``formatters._base.thin_sep``: "─" x 24).

_THIN_SEP = "\u2500" * 24

#: Risk thresholds matching ``formatters._base._risk_level`` (70 high / 40 medium).
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


def _lp_lock_pct(r: dict[str, Any]) -> float | None:
    """LP lock percentage (0-100) from any source, or None.

    Prefers the SENTINEL liquidity-verification percentage, then the legacy
    ``lp_locked`` fraction (0-1) or 0-100 value.
    """
    try:
        sentinel = r.get("sentinel") or {}
        free = sentinel.get("free") if isinstance(sentinel, dict) else None
        lv = free.get("liquidity_verification") if isinstance(free, dict) else None
        if isinstance(lv, dict) and lv.get("total_locked_percentage") is not None:
            return float(lv["total_locked_percentage"])
    except (TypeError, ValueError) as e:
        logger.debug("lp lock percentage parse failed: %s", e)
    raw = r.get("lp_locked")
    if raw is None:
        return None
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    return f * 100 if f <= 1 else f


def _mint_auth(r: dict[str, Any]) -> bool | None:
    """Mint-authority-active flag (True = dangerous), or None when unknown."""
    v = r.get("mintable")
    if v is None:
        rs = r.get("risk_signals")
        if isinstance(rs, dict):
            v = rs.get("mintable")
    if v is None:
        raw = r.get("mint_authority")
        if isinstance(raw, str) and raw.lower() not in ("", "unknown", "none", "null"):
            return True
    return v if isinstance(v, bool) else None


def _freeze_auth(r: dict[str, Any]) -> bool | None:
    """Freeze/blacklist-authority-active flag (True = dangerous), or None."""
    v = r.get("freezable")
    if v is None:
        rs = r.get("risk_signals")
        if isinstance(rs, dict):
            v = rs.get("freezable")
    if v is None:
        v = r.get("can_blacklist")
    if v is None:
        raw = r.get("freeze_authority")
        if isinstance(raw, str) and raw.lower() not in ("", "unknown", "none", "null"):
            return True
    return v if isinstance(v, bool) else None


def _float_or_none(value: Any) -> float | None:
    """Best-effort float conversion (N/A-safe) for report metrics."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bundle_wallet_count(ba: dict[str, Any]) -> int | None:
    """Number of wallets implicated in a bundled launch, or None.

    Prefers the launch-buyer count from ``detect_bundled_launch``, then the
    distinct buyers across cluster/bundle blocks, then common funders. Only
    counts wallets the engine actually identified — never fabricates a 0.
    """
    raw = ba.get("launch_buyers")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError) as e:
            logger.debug("token age parse failed: %s", e)
    for key in ("cluster_blocks", "bundle_blocks"):
        blocks = ba.get(key)
        if isinstance(blocks, list) and blocks:
            total = 0
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                try:
                    total += int(b.get("buyer_count") or 0)
                except (TypeError, ValueError):
                    continue
            if total > 0:
                return total
    funders = ba.get("common_funders")
    if isinstance(funders, list) and funders:
        return len(funders)
    return None


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
        risk = html.escape(str(ba.get("risk_label") or "unknown")).upper()
        conf_str = f"{conf:.0f}% conf" if conf is not None else "unknown conf"
        count = f" <b>{n}</b> wallets" if n is not None else ""
        return f"\U0001f534 Bundle detected:{count} \u00b7 {conf_str} \u00b7 {risk}"
    if str(ba.get("result") or "").lower() == "scanned":
        return "\U0001f7e2 No bundle detected"
    return "\u2014"


def _cex_funded_pct(r: dict[str, Any]) -> float | None:
    """% of analyzed holders funded from a CEX wallet, or None when unknown.

    Probes every engine payload that reports the number: the own wallet-
    metrics engine (``wallet_metrics.cex_funded_pct``), the SENTINEL holder
    analysis (``holder_analysis.exchange_funded_pct``), the own funding
    engine (``funding_analysis.exchange_funded_pct``) and the backend
    evidence bucket (``evidence.funding_analysis``). Returns the first
    number a provider actually reported — never a fabricated 0.
    """
    wm = r.get("wallet_metrics")
    if isinstance(wm, dict) and str(wm.get("result") or "") != "unavailable" and wm.get("cex_funded_pct") is not None:
        pct = _float_or_none(wm.get("cex_funded_pct"))
        if pct is not None:
            return pct
    ha = r.get("holder_analysis")
    if isinstance(ha, dict) and ha.get("exchange_funded_pct") is not None:
        pct = _float_or_none(ha.get("exchange_funded_pct"))
        if pct is not None:
            return pct
    fa = r.get("funding_analysis")
    if (
        isinstance(fa, dict)
        and str(fa.get("result") or "") != "unavailable"
        and fa.get("exchange_funded_pct") is not None
    ):
        pct = _float_or_none(fa.get("exchange_funded_pct"))
        if pct is not None:
            return pct
    ev = r.get("evidence")
    if isinstance(ev, dict):
        ef = ev.get("funding_analysis")
        if isinstance(ef, dict) and ef.get("exchange_funded_pct") is not None:
            pct = _float_or_none(ef.get("exchange_funded_pct"))
            if pct is not None:
                return pct
    # Legacy v2-to-legacy converter flattens the funding analysis into a
    # top-level ``exchange_funded_pct`` key (see tools/scanner/_flatten.py).
    if r.get("exchange_funded_pct") is not None:
        pct = _float_or_none(r.get("exchange_funded_pct"))
        if pct is not None:
            return pct
    return None


def _distribution_verdict(r: dict[str, Any]) -> tuple[str, str] | None:
    """One-line holder-distribution verdict (emoji, word), or None when no data.

    Thresholds mirror the canonical holder_clusters concentration bands
    (top-10 ≥ 50% concentrated / ≥ 30% moderate) and the SENTINEL HHI bands
    (≥ 3000 concentrated / ≥ 1500 moderate). Never claims a verdict when the
    scan carries no concentration data.
    """
    ha = r.get("holder_analysis")
    top10 = _float_or_none(ha.get("top_10_pct")) if isinstance(ha, dict) else None
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
    if isinstance(gmgn_holders, dict) and (gmgn_holders.get("sniper_count") or gmgn_holders.get("insider_count")):
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


def _holders_count(r: dict[str, Any]) -> int | None:
    """Total holder count from any source, or None.

    Probes every provider shape the scanners emit: the GMGN enrichment dict
    (``holder_count`` — normalized by ``gmgn_enrich._normalize_holders``),
    SENTINEL ``holder_analysis`` (``total_holders``), the legacy
    ``holder_analysis`` dict and the flat ``holders`` key. A missing count
    returns None (the formatter renders "—"/N/A) — never a fabricated 0.
    """
    gmgn_holders = (r.get("gmgn") or {}).get("holders") if isinstance(r.get("gmgn"), dict) else None
    sentinel_free = (r.get("sentinel") or {}).get("free") if isinstance(r.get("sentinel"), dict) else None
    sentinel_ha = sentinel_free.get("holder_analysis") if isinstance(sentinel_free, dict) else None
    for source in (
        gmgn_holders,
        sentinel_ha,
        r.get("holder_analysis"),
    ):
        if isinstance(source, dict):
            raw = source.get("holder_count", source.get("total_holders"))
            if raw is not None:
                try:
                    return int(float(raw))
                except (TypeError, ValueError) as e:
                    logger.debug("time-ago parse failed: %s", e)
    try:
        holders = r.get("holders")
        return int(float(holders)) if holders is not None else None
    except (TypeError, ValueError):
        return None


def _fake_volume_pct(r: dict[str, Any]) -> float | None:
    """Fake-volume / wash-trading percentage (0-100), or None."""
    wash = r.get("wash_trading")
    if isinstance(wash, dict) and wash.get("fake_volume_pct") is not None:
        try:
            return float(wash["fake_volume_pct"])
        except (TypeError, ValueError):
            return None
    ratio = r.get("volume_real_ratio")
    if ratio is None:
        return None
    try:
        rv = float(ratio)
    except (TypeError, ValueError):
        return None
    return min(100.0, max(0.0, (rv - 2.0) * 20.0)) if rv > 2.0 else 0.0


def _volume_up_price_flat_churn(r: dict[str, Any]) -> str | None:
    """Market-side churn note (volume up, price flat) — or None.

    Cheap independent wash signal mirroring the GMGN cross-reference: a token
    trading >$100k in 24h while the price moves <3% is churning volume without
    real buying pressure. Only ever AUGMENTS the engine's own on-chain verdict
    in the renderer — it never fires a fake-volume verdict on its own.
    """
    vol = _float_or_none(r.get("volume_24h"))
    pc = _float_or_none(r.get("price_change_24h"))
    if vol is None or pc is None:
        return None
    if vol > 100_000 and abs(pc) < 3:
        return "vol up, price flat"
    return None


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


def _fresh_wallet_pct(r: dict[str, Any]) -> float | None:
    """Fresh-wallet percentage (0-100), or None when no wallet metrics."""
    wm = r.get("wallet_metrics")
    if isinstance(wm, dict) and wm.get("result") != "unavailable" and wm.get("fresh_wallet_pct") is not None:
        try:
            return float(wm["fresh_wallet_pct"])
        except (TypeError, ValueError):
            return None
    raw = r.get("fresh_wallet_pct")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _tax_pct_str(value: Any) -> str:
    """Tax percent string from a 0-1 fraction or a 0-100 value (N/A-safe)."""
    if value is None:
        return "N/A"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return f"{f * 100:.1f}%" if f <= 1 else f"{f:.1f}%"


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
    rows.append(f"  Holders        <b>{holders:,}</b>" if holders is not None else "  Holders        <b>N/A</b>")

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
            f"  LP Lock        {_risk_emoji(lp)} <b>{lp:.0f}%</b>" if lp is not None else "  LP Lock        <b>N/A</b>"
        )
        bundle_row = _bundle_verdict_row(r)
        if bundle_row is not None:
            rows.append(f"  Bundle         {bundle_row}")
        cex = _cex_funded_pct(r)
        rows.append(
            f"  CEX-Funded     \U0001f3e6 <b>{cex:.1f}%</b>" if cex is not None else "  CEX-Funded     <b>\u2014</b>"
        )
        gmgn_holders = (r.get("gmgn") or {}).get("holders") if isinstance(r.get("gmgn"), dict) else None
        if isinstance(gmgn_holders, dict) and (gmgn_holders.get("sniper_count") or gmgn_holders.get("insider_count")):
            parts = []
            if gmgn_holders.get("sniper_count"):
                parts.append(f"\U0001f3af snipers <b>{int(gmgn_holders['sniper_count'])}</b>")
            if gmgn_holders.get("insider_count"):
                parts.append(f"\U0001f575\ufe0f insiders <b>{int(gmgn_holders['insider_count'])}</b>")
            sep_dot = " \u00b7 "
            rows.append(f"  Snipers        {sep_dot.join(parts)}")

    return ["", "\U0001f4ca <b>Key Metrics</b>", _THIN_SEP, *rows]


def _token_2022_flag(r: dict[str, Any]) -> str | None:
    """Token-2022 program flag (Solana) — None when the scan carries no data."""
    if r.get("token_2022"):
        return "Token-2022 program — extensions may hide transfer hooks / freeze"
    if str(r.get("token_program") or "").lower() in ("spl-token-2022", "tokenzqdbnblqap5vehdkas6epflc1phnbqcxeppxueb"):
        return "Token-2022 program — extensions may hide transfer hooks / freeze"
    try:
        sentinel = r.get("sentinel") or {}
        free = sentinel.get("free") if isinstance(sentinel, dict) else None
        mf = free.get("metadata_fingerprint") if isinstance(free, dict) else None
        if isinstance(mf, dict) and str(mf.get("token_program") or "").lower() in (
            "spl-token-2022",
            "token-2022",
        ):
            return "Token-2022 program — extensions may hide transfer hooks / freeze"
    except (AttributeError, TypeError) as e:
        logger.debug("bonding curve parse failed: %s", e)
    return None


def _mev_note(r: dict[str, Any]) -> str | None:
    """MEV note — only when the scan actually carried MEV risk data."""
    try:
        sentinel = r.get("sentinel") or {}
        free = sentinel.get("free") if isinstance(sentinel, dict) else None
        mev = free.get("mev_detection") if isinstance(free, dict) else None
        if isinstance(mev, dict):
            level = str(mev.get("risk_level") or "").lower()
            if level in ("high", "critical"):
                return f"MEV risk <b>{html.escape(level.upper())}</b> — sandwich / front-run exposure"
            warnings = mev.get("warnings")
            if isinstance(warnings, list) and warnings:
                return f"MEV: {html.escape(str(warnings[0])[:90])}"
    except (AttributeError, TypeError) as e:
        logger.debug("market stats parse failed: %s", e)
    return None


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
    if isinstance(gmgn_holders, dict) and (gmgn_holders.get("sniper_count") or gmgn_holders.get("insider_count")):
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
    ba = r.get("bundle_analysis")
    if isinstance(ba, dict) and ba.get("is_bundled"):
        return "Bundled launch \u2014 volume/concentration may be fabricated; don't trust momentum."
    fake_vol = _fake_volume_pct(r)
    if fake_vol is not None and fake_vol > 10:
        return "Fake volume suspected \u2014 price action may not be real."
    fresh = _fresh_wallet_pct(r)
    if fresh is not None and fresh > 50:
        return "Fresh wallets dominate \u2014 likely a sniped / coordinated launch."
    return "Standard caution \u2014 size in small and watch sell pressure on the first green candle."


# ── Scam Signals matrix (complete technique coverage, honest "—") ─────────
# The consolidated risk matrix: every modern crypto scam technique gets a
# row with one of three states —
#   🔴 signal FIRED (technique detected as present)
#   ✅ technique CHECKED and clean
#   —  no data (keep-it-honest: never claim "clean" without a check)
# Rows probe every provider shape the scanners emit: the flat legacy keys,
# the ``evidence.security`` GoPlus/own payload, the ``bundle_analysis`` /
# ``wash_trading`` engines and the ``risk_signals`` enrichment dict.

_SCAM_SIGNAL_RED = "\U0001f534"
_SCAM_SIGNAL_GREEN = "\u2705"
_SCAM_SIGNAL_UNKNOWN = "\u2014"


def _evidence_security(r: dict[str, Any]) -> dict[str, Any]:
    """Best-effort GoPlus/own security evidence dict from any report shape."""
    ev = r.get("evidence")
    if isinstance(ev, dict):
        sec = ev.get("security")
        if isinstance(sec, dict):
            return sec
    sec = r.get("security")
    return sec if isinstance(sec, dict) else {}


def _risk_flag_set(r: dict[str, Any]) -> set[str]:
    """Normalized set of risk flags / threats from any report shape."""
    flags: set[str] = set()
    for key in ("threats", "risk_flags"):
        raw = r.get(key)
        if isinstance(raw, list):
            flags.update(str(f).lower() for f in raw if f)
    rs = r.get("risk_signals")
    if isinstance(rs, dict):
        score = rs.get("rug_risk_score")
        if isinstance(score, dict):
            sf = score.get("flags")
            if isinstance(sf, list):
                flags.update(str(f).lower() for f in sf if f)
    return flags


def _honeypot_flat(r: dict[str, Any]) -> bool | None:
    """Honeypot flag from flat report keys (bool or 'detected'/'none')."""
    raw = r.get("honeypot")
    if raw is None:
        sec = _evidence_security(r)
        raw = sec.get("is_honeypot")
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in ("detected", "yes", "true", "1"):
        return True
    if s in ("none", "no", "false", "0", "clean"):
        return False
    return None


def _scam_signal_rows(r: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Build the (label, state, detail) rows for the Scam Signals matrix.

    state is one of ``_SCAM_SIGNAL_RED`` / ``_SCAM_SIGNAL_GREEN`` /
    ``_SCAM_SIGNAL_UNKNOWN``. Detail is HTML-escaped by the caller. Every
    technique is listed — a missing data source renders as "—", never as a
    fabricated "clean".
    """
    sec = _evidence_security(r)
    flags = _risk_flag_set(r)
    rows: list[tuple[str, str, str]] = []

    # 1. Honeypot (sell-block)
    hp = _honeypot_flat(r)
    if hp is True or "honeypot" in flags:
        rows.append(("Honeypot (sell-block)", _SCAM_SIGNAL_RED, "sells may be blocked"))
    elif hp is False:
        rows.append(("Honeypot (sell-block)", _SCAM_SIGNAL_GREEN, "sellable"))
    else:
        rows.append(("Honeypot (sell-block)", _SCAM_SIGNAL_UNKNOWN, ""))

    # 2. Rug / LP removal risk
    selfdestruct = sec.get("selfdestruct")
    lp = _lp_lock_pct(r)
    if selfdestruct is True or (lp is not None and lp < 50):
        detail = "contract selfdestruct" if selfdestruct is True else f"LP only {lp:.0f}% locked"
        rows.append(("Rug / LP removal", _SCAM_SIGNAL_RED, detail))
    elif lp is not None:
        rows.append(("Rug / LP removal", _SCAM_SIGNAL_GREEN, f"LP {lp:.0f}% locked"))
    elif selfdestruct is False:
        rows.append(("Rug / LP removal", _SCAM_SIGNAL_GREEN, "no selfdestruct"))
    else:
        rows.append(("Rug / LP removal", _SCAM_SIGNAL_UNKNOWN, ""))

    # 3. Bundle / sniper launch
    ba = r.get("bundle_analysis")
    bundled = ba.get("is_bundled") if isinstance(ba, dict) else None
    if bundled is True or r.get("bundle_detected") or "bundled_launch" in flags:
        n = _bundle_wallet_count(ba) if isinstance(ba, dict) else None
        detail = f"{n} wallets" if n is not None else "coordinated launch"
        rows.append(("Bundle / sniper", _SCAM_SIGNAL_RED, detail))
    elif isinstance(ba, dict) and bundled is False:
        rows.append(("Bundle / sniper", _SCAM_SIGNAL_GREEN, "no bundle"))
    else:
        rows.append(("Bundle / sniper", _SCAM_SIGNAL_UNKNOWN, ""))

    # 4. Same-slot bundler (Solana)
    same_wallets = ba.get("same_slot_wallets") if isinstance(ba, dict) else None
    if isinstance(same_wallets, (int, float)) and same_wallets >= 3:
        rows.append(("Same-slot bundler", _SCAM_SIGNAL_RED, f"{int(same_wallets)} wallets same slot"))
    elif isinstance(same_wallets, (int, float)):
        rows.append(("Same-slot bundler", _SCAM_SIGNAL_GREEN, f"{int(same_wallets)} same-slot"))
    else:
        rows.append(("Same-slot bundler", _SCAM_SIGNAL_UNKNOWN, ""))

    # 5. Funding-chain (CEX-funded / coordinated funding)
    cex = _cex_funded_pct(r)
    if cex is not None:
        if cex > 60:
            rows.append(("Funding-chain", _SCAM_SIGNAL_RED, f"{cex:.0f}% CEX-funded"))
        else:
            rows.append(("Funding-chain", _SCAM_SIGNAL_GREEN, f"{cex:.0f}% CEX-funded"))
    else:
        rows.append(("Funding-chain", _SCAM_SIGNAL_UNKNOWN, ""))

    # 6. Fake volume (wash trading)
    fake_vol = _fake_volume_pct(r)
    if fake_vol is not None:
        if fake_vol > 10:
            rows.append(("Fake volume (wash)", _SCAM_SIGNAL_RED, f"~{fake_vol:.0f}% fabricated"))
        else:
            rows.append(("Fake volume (wash)", _SCAM_SIGNAL_GREEN, f"~{fake_vol:.0f}% fabricated"))
    elif "wash_trading" in flags:
        rows.append(("Fake volume (wash)", _SCAM_SIGNAL_RED, "flagged"))
    else:
        rows.append(("Fake volume (wash)", _SCAM_SIGNAL_UNKNOWN, ""))

    # 7. Deployer dump (team wallet)
    dump = sec.get("deployer_dump_detected")
    if dump is True:
        count = sec.get("deployer_dump_count")
        detail = f"{count} deployer sell txns" if isinstance(count, (int, float)) and count else "deployer selling"
        rows.append(("Deployer dump", _SCAM_SIGNAL_RED, detail))
    elif dump is False:
        rows.append(("Deployer dump", _SCAM_SIGNAL_GREEN, "no team sell detected"))
    elif "malicious_deployer" in flags or "repeat_deployer" in flags or "high_risk_deployer" in flags:
        rows.append(("Deployer dump", _SCAM_SIGNAL_RED, "repeat / malicious deployer"))
    else:
        rows.append(("Deployer dump", _SCAM_SIGNAL_UNKNOWN, ""))

    # 8. Mint authority
    mint = _mint_auth(r)
    if mint is True:
        rows.append(("Mint authority", _SCAM_SIGNAL_RED, "supply can be minted"))
    elif mint is False:
        rows.append(("Mint authority", _SCAM_SIGNAL_GREEN, "renounced"))
    else:
        rows.append(("Mint authority", _SCAM_SIGNAL_UNKNOWN, ""))

    # 9. Freeze / blacklist authority
    freeze = _freeze_auth(r)
    if freeze is True:
        rows.append(("Freeze authority", _SCAM_SIGNAL_RED, "wallets can be frozen"))
    elif freeze is False:
        rows.append(("Freeze authority", _SCAM_SIGNAL_GREEN, "renounced"))
    else:
        rows.append(("Freeze authority", _SCAM_SIGNAL_UNKNOWN, ""))

    # 10. Tax manipulation (buy/sell/transfer)
    buy_tax = r.get("buy_tax") if r.get("buy_tax") is not None else sec.get("buy_tax")
    sell_tax = r.get("sell_tax") if r.get("sell_tax") is not None else sec.get("sell_tax")
    transfer_flag = sec.get("transfer_pausable") is True or sec.get("trading_cooldown") is True
    slippage_flag = sec.get("slippage_modifiable") is True

    def _tax_pct(value: Any) -> float | None:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return f * 100 if f <= 1 else f

    tax_high = any((p := _tax_pct(t)) is not None and p > 10 for t in (buy_tax, sell_tax))
    if tax_high or transfer_flag or slippage_flag:
        detail = "transfer pausable" if sec.get("transfer_pausable") is True else "buy/sell tax >10%"
        rows.append(("Tax manipulation", _SCAM_SIGNAL_RED, detail))
    elif buy_tax is not None or sell_tax is not None:
        rows.append(("Tax manipulation", _SCAM_SIGNAL_GREEN, "taxes within range"))
    else:
        rows.append(("Tax manipulation", _SCAM_SIGNAL_UNKNOWN, ""))

    # 11. Proxy / upgradeable
    proxy = sec.get("is_proxy")
    if proxy is True:
        rows.append(("Proxy / upgradeable", _SCAM_SIGNAL_RED, "logic can be swapped"))
    elif proxy is False:
        rows.append(("Proxy / upgradeable", _SCAM_SIGNAL_GREEN, "not a proxy"))
    else:
        rows.append(("Proxy / upgradeable", _SCAM_SIGNAL_UNKNOWN, ""))

    # 12. Owner-renounce lies (hidden owner / take-back / balance change)
    lie_flags = sec.get("can_take_back_ownership") is True or sec.get("hidden_owner") is True
    lie_flags = lie_flags or sec.get("owner_change_balance") is True
    if lie_flags:
        detail = (
            "hidden owner"
            if sec.get("hidden_owner") is True
            else "can take back ownership"
            if sec.get("can_take_back_ownership") is True
            else "owner changes balances"
        )
        rows.append(("Owner-renounce lies", _SCAM_SIGNAL_RED, detail))
    elif sec.get("owner_renounced") is True:
        rows.append(("Owner-renounce lies", _SCAM_SIGNAL_GREEN, "renounced"))
    else:
        rows.append(("Owner-renounce lies", _SCAM_SIGNAL_UNKNOWN, ""))

    # 13. Address poisoning / dusting
    dust = sec.get("dusting_detected")
    if dust is True:
        n = sec.get("dust_recipients")
        detail = f"{n} addresses dusted" if isinstance(n, (int, float)) and n else "dust campaign"
        rows.append(("Address poisoning / dusting", _SCAM_SIGNAL_RED, detail))
    elif dust is False:
        rows.append(("Address poisoning / dusting", _SCAM_SIGNAL_GREEN, "no dusting"))
    else:
        rows.append(("Address poisoning / dusting", _SCAM_SIGNAL_UNKNOWN, ""))

    # 14. Drainer contract (known scam / spam registry hit)
    rs = r.get("risk_signals")
    known_scammer = rs.get("known_scammer") if isinstance(rs, dict) else None
    if known_scammer is True or "flagged_spam" in flags or "known_scam" in flags:
        rows.append(("Drainer / known scam", _SCAM_SIGNAL_RED, "registry hit"))
    elif known_scammer is False:
        rows.append(("Drainer / known scam", _SCAM_SIGNAL_GREEN, "not in scam registry"))
    else:
        rows.append(("Drainer / known scam", _SCAM_SIGNAL_UNKNOWN, ""))

    # 15. Token-2022 (transfer hooks / close-mint / permanent delegate)
    token_2022 = _token_2022_flag(r)
    if token_2022:
        rows.append(("Token-2022 extensions", _SCAM_SIGNAL_RED, "hooks/freeze may hide risk"))
    else:
        rows.append(("Token-2022 extensions", _SCAM_SIGNAL_UNKNOWN, ""))

    # 16. Cloned contract (metadata clone of a known project)
    if "copycat_token" in flags or sec.get("is_copycat") is True:
        rows.append(("Cloned contract", _SCAM_SIGNAL_RED, "copycat / metadata clone"))
    elif sec.get("is_copycat") is False:
        rows.append(("Cloned contract", _SCAM_SIGNAL_GREEN, "not a clone"))
    else:
        rows.append(("Cloned contract", _SCAM_SIGNAL_UNKNOWN, ""))

    return rows


def _format_scam_signals_section(r: dict[str, Any]) -> list[str]:
    """Render the consolidated Scam Signals risk matrix (always 16 rows).

    Every modern scam technique is listed with a 🔴 / ✅ / — state so the
    report is a complete coverage matrix: 🔴 when the signal fired, ✅ when
    the check ran clean, — when no data was available (never claimed clean
    without a check). Returns an empty list only for an empty report dict.
    """
    if not isinstance(r, dict) or not r:
        return []
    rows = _scam_signal_rows(r)
    lines = ["", "\U0001f50d <b>Scam Signals</b>", _THIN_SEP]
    for label, state, detail in rows:
        suffix = f" \u2014 {html.escape(detail)}" if detail else ""
        lines.append(f"  {state} {html.escape(label)}{suffix}")
    return lines
