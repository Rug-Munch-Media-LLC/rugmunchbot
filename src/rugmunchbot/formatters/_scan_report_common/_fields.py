"""Report-field extractors — probe every provider shape, never fabricate."""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)


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
    if (
        isinstance(wm, dict)
        and str(wm.get("result") or "") != "unavailable"
        and wm.get("cex_funded_pct") is not None
    ):
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


def _holders_count(r: dict[str, Any]) -> int | None:
    """Total holder count from any source, or None.

    Probes every provider shape the scanners emit: the GMGN enrichment dict
    (``holder_count`` — normalized by ``gmgn_enrich._normalize_holders``),
    SENTINEL ``holder_analysis`` (``total_holders``), the legacy
    ``holder_analysis`` dict and the flat ``holders`` key. A missing count
    returns None (the formatter renders "—"/N/A) — never a fabricated 0.
    """
    gmgn_holders = (r.get("gmgn") or {}).get("holders") if isinstance(r.get("gmgn"), dict) else None
    sentinel_free = (
        (r.get("sentinel") or {}).get("free") if isinstance(r.get("sentinel"), dict) else None
    )
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
    # v2 scanner evidence bucket (``evidence.holders.holder_count``) — the
    # canonical holder source the unified engine merges into the payload;
    # without this probe v2-backed notes render "N/A" holders.
    ev = r.get("evidence")
    if isinstance(ev, dict):
        ev_holders = ev.get("holders")
        if isinstance(ev_holders, dict):
            raw = ev_holders.get("holder_count", ev_holders.get("total_holders"))
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
    # v2 evidence: the backend's volume_authenticity deep phase reports
    # ``evidence.market.fake_volume_pct`` (merged into the payload by
    # _merge_completed_scan / v2_token_to_legacy).
    ev = r.get("evidence")
    if isinstance(ev, dict):
        mkt = ev.get("market")
        if isinstance(mkt, dict) and mkt.get("fake_volume_pct") is not None:
            try:
                return float(mkt["fake_volume_pct"])
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


def _fresh_wallet_pct(r: dict[str, Any]) -> float | None:
    """Fresh-wallet percentage (0-100), or None when no wallet metrics."""
    wm = r.get("wallet_metrics")
    if (
        isinstance(wm, dict)
        and wm.get("result") != "unavailable"
        and wm.get("fresh_wallet_pct") is not None
    ):
        try:
            return float(wm["fresh_wallet_pct"])
        except (TypeError, ValueError):
            return None
    ha = r.get("holder_analysis")
    if isinstance(ha, dict) and ha.get("new_wallets_pct") is not None:
        try:
            return float(ha["new_wallets_pct"])
        except (TypeError, ValueError):
            pass
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


def _token_2022_flag(r: dict[str, Any]) -> str | None:
    """Token-2022 program flag (Solana) — None when the scan carries no data."""
    if r.get("token_2022"):
        return "Token-2022 program — extensions may hide transfer hooks / freeze"
    if str(r.get("token_program") or "").lower() in (
        "spl-token-2022",
        "tokenzqdbnblqap5vehdkas6epflc1phnbqcxeppxueb",
    ):
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
                return (
                    f"MEV risk <b>{html.escape(level.upper())}</b> — sandwich / front-run exposure"
                )
            warnings = mev.get("warnings")
            if isinstance(warnings, list) and warnings:
                return f"MEV: {html.escape(str(warnings[0])[:90])}"
    except (AttributeError, TypeError) as e:
        logger.debug("market stats parse failed: %s", e)
    return None
