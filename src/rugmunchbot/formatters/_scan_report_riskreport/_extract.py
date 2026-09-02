"""Section extraction for the RiskReport (mirrors risk_report._extract_*).

Split out of ``formatters/_scan_report_riskreport.py`` during the de-godding
pass. Each ``_extract_*`` pulls one report section (deployer / holders /
first-buyer / bundle / honeypot / liquidity) out of the scan payload,
defensively, never fabricating a value. Pure module: no network, no I/O.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._scan_report_common._fields import (
    _freeze_auth,
    _holders_count,
    _lp_lock_pct,
    _mint_auth,
)
from rugmunchbot.formatters._scan_report_riskreport._constants import (
    _f,
    _first_buyer_dict,
    _i,
    _sec,
    _sentinel_module,
)

# ═══════════════════════════════════════════════════════════════════════════
# SECTION EXTRACTION (mirrors risk_report._extract_*)
# ═══════════════════════════════════════════════════════════════════════════


def _extract_deployer_section(r: dict[str, Any]) -> dict[str, Any]:
    """Deployer section from deployer_profile / evidence / flat address."""
    dp = r.get("deployer_profile")
    if not isinstance(dp, dict):
        dp = {}
    dpr = (r.get("evidence") or {}).get("deployer")
    if not isinstance(dpr, dict):
        dpr = {}

    address = (
        r.get("deployer_address")
        or r.get("dev_wallet")
        or dpr.get("deployer_address")
        or dpr.get("address")
        or dp.get("address")
    )
    total = _i(
        dp.get("total_deployments")
        if dp.get("total_deployments") is not None
        else dpr.get("total_deployments", dpr.get("total_launches"))
    )
    rug_rate = _f(dp.get("rug_rate") if dp.get("rug_rate") is not None else dpr.get("rug_rate"))
    bucket = str(dp.get("risk_bucket") or dpr.get("risk_bucket") or "").upper()
    funder = dpr.get("funder_profile")
    if not isinstance(funder, dict):
        funder = {}
    funded_from = str(dp.get("funded_from") or funder.get("funding_type") or "").lower()

    repeat: bool | None = total > 1 if total else None
    previously_rugged: bool | None
    if rug_rate is not None:
        previously_rugged = rug_rate >= 0.5
    elif bucket:
        previously_rugged = bucket in ("REPEAT_RUG", "FARM")
    else:
        previously_rugged = None

    fresh_wallet: bool | None
    age_days = _f(dp.get("wallet_age_days"))
    fresh_wallet = age_days < 30 if age_days is not None else None

    funder_cex = bool(funder.get("is_cex")) or funded_from in (
        "coinbase",
        "binance",
        "okx",
        "bybit",
        "kraken",
        "cex",
    )
    funder_fresh = bool(funder.get("is_fresh")) or funded_from in ("fresh_wallet", "tornado")

    return {
        "address": address,
        "repeat_deployer": repeat,
        "previously_rugged": previously_rugged,
        "total_deployments": total,
        "risk_bucket": bucket or None,
        "fresh_wallet": fresh_wallet,
        "funder": {
            "address": funder.get("funder_address") or funder.get("address"),
            "is_cex": funder_cex,
            "cex_label": funder.get("cex_label"),
            "is_fresh": funder_fresh,
        },
    }


def _top_25_list(r: dict[str, Any]) -> tuple[list[dict[str, Any]], float | None]:
    """Top-25 holder list + summed top-25 share (None when <10 entries)."""
    top: list[dict[str, Any]] = []
    seen: set[str] = set()
    ev = r.get("evidence")
    ev_holders = ev.get("holders") if isinstance(ev, dict) else None
    for source in (r.get("top_holders"), ev_holders):
        raw: Any
        if isinstance(source, list):
            raw = source
        elif isinstance(source, dict):
            raw = source.get("top_holders")
        else:
            raw = None
        if not isinstance(raw, list):
            continue
        for h in raw:
            if not isinstance(h, dict):
                continue
            addr = str(h.get("address") or "")
            if addr and addr in seen:
                continue
            if addr:
                seen.add(addr)
            pct = _f(h.get("pct", h.get("percentage", h.get("share_pct", h.get("balance_pct")))))
            top.append(
                {
                    "address": addr,
                    "share_pct": pct,
                    "label": h.get("label") or h.get("tag"),
                    "cluster_id": h.get("cluster_id"),
                }
            )
            if len(top) >= 25:
                break
        if top:
            break
    top25_pct: float | None = None
    pcts = [h["share_pct"] for h in top if h.get("share_pct") is not None]
    if len(pcts) >= 10:
        top25_pct = round(min(sum(pcts[:25]), 100.0), 1)
    return top, top25_pct


def _extract_holders_section(r: dict[str, Any], deployer: str | None) -> dict[str, Any]:
    """Holders section: total, top-10, fresh%, CEX%, dev/team share."""
    ha = r.get("holder_analysis")
    if not isinstance(ha, dict):
        ha = {}
    ev = r.get("evidence")
    ev_holders = ev.get("holders") if isinstance(ev, dict) else None
    if not isinstance(ev_holders, dict):
        ev_holders = {}
    sent_ha = _sentinel_module(r, "holder_analysis")

    top10 = _f(ha.get("top_10_pct") or ha.get("top_10_percentage"))
    if top10 is None:
        top10 = _f(ev_holders.get("top_10_pct", ev_holders.get("top10_pct")))
    if top10 is None:
        top10 = _f(sent_ha.get("top10_percentage", sent_ha.get("top_10_percentage")))
    onchain = r.get("onchain")
    if top10 is None and isinstance(onchain, dict):
        top10 = _f(onchain.get("top_10_pct"))

    gini = _f(ev_holders.get("gini_coefficient", ev_holders.get("gini")))
    if gini is None:
        gini = _f(ha.get("gini") or sent_ha.get("gini"))

    return {
        "total_holders": _holders_count(r),
        "top_10_pct": top10,
        "gini": gini,
        "concentration": ha.get("hhi_risk_level") or sent_ha.get("risk_level") or "unknown",
    }


def _dev_team_share(top: list[dict[str, Any]], deployer: str | None) -> float | None:
    """Top-25 share held by the deployer + team-labelled wallets."""
    deployer_l = (deployer or "").lower()
    share = 0.0
    counted = False
    for h in top:
        addr = str(h.get("address") or "").lower()
        pct = h.get("share_pct")
        label = str(h.get("label") or "").lower()
        is_team = bool(addr and deployer_l and addr == deployer_l) or label in ("team", "deployer")
        if not is_team:
            continue
        p = _f(pct)
        if p is None:
            continue
        share += p
        counted = True
    return round(share, 1) if counted else None


def _extract_first_buyer_section(
    r: dict[str, Any], deployer: str | None, bundle: dict[str, Any]
) -> dict[str, Any]:
    """First-buyer section: address, is_deployer, in_bundle (+ cohort depth)."""
    fb = _first_buyer_dict(r)
    if not fb:
        return {}
    addr = fb.get("address") or fb.get("buyer") or fb.get("wallet")
    is_deployer: bool | None = None
    if addr and deployer and str(addr).lower() == deployer.lower():
        is_deployer = True
    in_bundle: bool | None = None
    if isinstance(fb.get("bundled"), bool):
        in_bundle = fb["bundled"]
    if in_bundle is None and isinstance(bundle, dict) and bundle.get("is_bundled") is True:
        launch_buyers = bundle.get("launch_buyers")
        if isinstance(launch_buyers, list):
            for lb in launch_buyers:
                lb_addr = lb.get("address") if isinstance(lb, dict) else lb
                if lb_addr and addr and str(lb_addr).lower() == str(addr).lower():
                    in_bundle = True
                    break
        if in_bundle is None:
            in_bundle = True if isinstance(fb.get("bundled"), bool) and fb["bundled"] else None
    return {
        "address": addr,
        "is_deployer": is_deployer,
        "in_bundle": in_bundle,
        "block": fb.get("block_number", fb.get("block")),
        "tx": fb.get("tx_hash") or fb.get("first_buy_ts"),
        "cohort_size": fb.get("cohort_size"),
        "cohort_limit": fb.get("cohort_limit"),
        "cohort_fresh_pct": fb.get("cohort_fresh_pct"),
        "cohort_bundled_pct": fb.get("cohort_bundled_pct"),
    }


def _extract_bundle_section(r: dict[str, Any]) -> dict[str, Any]:
    """Bundle section from bundle_analysis (own engine + Solana same-slot)."""
    ba = r.get("bundle_analysis")
    if not isinstance(ba, dict):
        return {}
    sniper_count = 0
    blocks = ba.get("cluster_blocks")
    if isinstance(blocks, list):
        for b in blocks:
            if isinstance(b, dict):
                sniper_count += _i(b.get("buyer_count"))
    return {
        "is_bundled": ba.get("is_bundled"),
        "confidence": _f(ba.get("confidence")),
        "launch_cluster_score": _f(ba.get("launch_cluster_score")),
        "first_time_buyer_pct": _f(ba.get("first_time_buyer_pct")),
        "deployer_funded_pct": _f(ba.get("deployer_funded_pct")),
        "sniper_first_buyers": sniper_count,
        "launch_buyers": ba.get("launch_buyers"),
        "source": "bundle_analysis",
    }


def _honeypot_bool(r: dict[str, Any]) -> bool | None:
    """Honeypot boolean from every source the payload may carry (None=unknown)."""
    raw = r.get("honeypot")
    if raw is True or str(raw).lower() == "detected":
        return True
    if raw is False or str(raw).lower() in ("none", "no"):
        return False
    rs = r.get("risk_signals")
    if isinstance(rs, dict):
        v = rs.get("honeypot")
        if v is True or str(v).lower() == "detected":
            return True
        if v is False or str(v).lower() in ("none", "no"):
            return False
    sent_hp = _sentinel_module(r, "honeypot_detection")
    v = sent_hp.get("is_honeypot")
    if v is not None:
        return bool(v)
    sec = _sec(r)
    v = sec.get("is_honeypot")
    if v is not None:
        return bool(v)
    checks = sec.get("checks")
    if isinstance(checks, dict):
        hp = checks.get("HP01") or checks.get("HP04")
        if isinstance(hp, dict):
            if hp.get("status") == "fail":
                return True
            if hp.get("status") == "pass":
                return False
    return None


def _extract_honeypot_section(r: dict[str, Any]) -> dict[str, Any]:
    """Honeypot + mint/freeze/ownership + taxes section."""
    sec = _sec(r)
    sent_hp = _sentinel_module(r, "honeypot_detection")
    sent_ca = _sentinel_module(r, "contract_authority")

    buy_tax = _f(sec.get("buy_tax_pct", sec.get("buy_tax")))
    sell_tax = _f(sec.get("sell_tax_pct", sec.get("sell_tax")))
    if buy_tax is None:
        buy_tax = _f(sent_hp.get("buy_tax"))
    if sell_tax is None:
        sell_tax = _f(sent_hp.get("sell_tax"))
    # Legacy flat fields are fractions (0-1); SENTINEL/evidence are 0-100.
    if buy_tax is not None and buy_tax <= 1 and buy_tax > 0:
        buy_tax *= 100
    if sell_tax is not None and sell_tax <= 1 and sell_tax > 0:
        sell_tax *= 100

    mintable = _mint_auth(r)
    if mintable is None and isinstance(sent_ca, dict):
        mintable = sent_ca.get("can_mint", sent_ca.get("mint_authority_active"))
    freezable = _freeze_auth(r)
    if freezable is None and isinstance(sent_ca, dict):
        freezable = sent_ca.get("can_freeze", sent_ca.get("freeze_authority_active"))
    renounced = r.get("owner_renounced")
    if renounced is None:
        renounced = sec.get("ownership_renounced", sec.get("is_owner_renounced"))
    if renounced is None:
        renounced = sent_ca.get("is_ownership_renounced")

    return {
        "is_honeypot": _honeypot_bool(r),
        "buy_tax": buy_tax,
        "sell_tax": sell_tax,
        "mintable": mintable,
        "freezable": freezable,
        "ownership_renounced": renounced if isinstance(renounced, bool) else None,
        "warnings": sent_hp.get("warnings") or [],
    }


def _extract_liquidity_section(r: dict[str, Any]) -> dict[str, Any]:
    """Liquidity section: locked %, is_locked, USD amount."""
    lv = r.get("liquidity_verification")
    if not isinstance(lv, dict):
        lv = {}
    locked = _lp_lock_pct(r)
    liq_usd = _f(r.get("liquidity_usd"))
    if liq_usd is None:
        liq_usd = _f(r.get("liquidity"))
    return {
        "locked_pct": locked,
        "is_locked": lv.get("is_locked")
        if lv.get("is_locked") is not None
        else (locked is not None and locked >= 50),
        "is_burned": lv.get("lp_burned"),
        "lock_contract": lv.get("lock_contract"),
        "liquidity_usd": liq_usd,
    }
