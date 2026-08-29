"""Risk Report section for the scan report — the full product demo.

Wires the structured RiskReport data model from
``rugmunch_scanner/unified/risk_report.py`` into the bot's scan formatter:

  deployer / holders / first-buyer / top-25 / fresh% / CEX-funded% /
  bundle / cluster / honeypot / liquidity + the ~30-factor risk score +
  rug/scam determination.

Two data paths, one renderer:

* ``r["risk_report"]`` — the serialized RiskReport dict (``RiskReport.to_dict``)
  when the backend's scanner wired the phase. Rendered as-is.
* Otherwise ``_build_risk_report_from_scan(r)`` synthesizes the SAME dict
  shape from the scan payload the bot already carries (security evidence,
  SENTINEL modules, bundle analysis, wallet metrics, GMGN holders). The
  factor keys / labels / weights / bands mirror the canonical scanner module
  exactly — keep them in sync when ``rugmunch_scanner/unified/risk_report.py``
  changes (see the provenance note on each mirror table).

Rendering contract (Telegram HTML, fail-open):

* Basic scans (``deep=False``) — CONCISE: composite score + verdict +
  coverage + one line per section + the top critical/risk factors.
* Deeper scans (``deep=True``) — FULL: the same headline, the complete
  ~30-factor table and the deeper-level narrative, with the AI-assisted
  marker when an LLM pass ran.

Pure module: no network, no I/O; every missing value renders "—"/unknown,
never a fabricated number.
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.formatters._base import thin_sep
from rugmunchbot.formatters._scan_report_common._fields import (
    _cex_funded_pct,
    _fake_volume_pct,
    _float_or_none,
    _freeze_auth,
    _fresh_wallet_pct,
    _holders_count,
    _lp_lock_pct,
    _mint_auth,
)

# ═══════════════════════════════════════════════════════════════════════════
# CANONICAL MIRROR — keep in sync with rugmunch_scanner/unified/risk_report.py
# ═══════════════════════════════════════════════════════════════════════════
# Factor keys, labels, weights and band thresholds below are copied verbatim
# from the scanner's RiskReport module so a bot-built report scores and
# renders identically to a backend-provided one. When the canonical file
# changes, update BOTH tables and the band constants.

_CRITICAL_MIN = 70.0  # factor score >= 70 → CRITICAL
_RISK_MIN = 25.0  # factor score >= 25 → RISK (else OK)

_SCAM_MIN = 85.0  # composite >= 85 → scam (or confirmed honeypot)
_RUG_MIN = 70.0  # composite >= 70 → rug (or rugged-dev + bundle)
_RISKY_MIN = 45.0  # composite >= 45 → risky
_MIN_KNOWN_FOR_DETERMINATION = 6  # need >= 6 known factors to declare a verdict

_FACTOR_LABELS: dict[str, str] = {
    "deployer_repeat": "Repeat deployer",
    "deployer_rugged_before": "Deployer previously rugged",
    "deployer_new_wallet": "Fresh deployer wallet",
    "deployer_funding": "Deployer funding source",
    "holder_concentration": "Holder concentration (top-10)",
    "dev_team_share": "Dev/team wallet share",
    "top_holder_whale": "Whale top holder",
    "fresh_wallet_pct": "Fresh wallet share",
    "cex_funded_wallet_pct": "CEX-funded wallet share",
    "first_buyer_deployer": "First buyer is deployer",
    "first_buyer_bundle": "First buyer in bundle",
    "bundle_launch": "Bundled launch",
    "sniper_first_buyers": "Sniper first-buyers",
    "funder_cluster": "Shared-funder cluster",
    "cross_token_cluster": "Cross-token wallet cluster",
    "shared_deployer_network": "Shared-deployer network",
    "cross_token_funder": "Cross-token funder graph",
    "temporal_cluster": "Lockstep entry (temporal) cluster",
    "sniper_bot_pattern": "Sniper/bot launch pattern",
    "mev_bundle": "MEV bundle buys",
    "wash_trading_cluster": "Wash-trading cluster",
    "honeypot": "Honeypot (cannot sell)",
    "mint_authority": "Mint authority active",
    "freeze_authority": "Freeze authority active",
    "ownership_renounced": "Ownership not renounced",
    "lp_locked": "Liquidity locked",
    "lp_liquidity_amount": "Liquidity amount",
    "buy_sell_tax": "Buy/sell tax",
    "volume_authenticity": "Volume authenticity",
    "holder_overlap_scam": "Holder overlap with known scams",
}

_FACTOR_WEIGHTS: dict[str, float] = {
    "deployer_repeat": 2.0,
    "deployer_rugged_before": 3.0,
    "deployer_new_wallet": 1.5,
    "deployer_funding": 1.5,
    "holder_concentration": 2.5,
    "dev_team_share": 2.5,
    "top_holder_whale": 1.5,
    "fresh_wallet_pct": 2.0,
    "cex_funded_wallet_pct": 1.0,
    "first_buyer_deployer": 1.5,
    "first_buyer_bundle": 2.0,
    "bundle_launch": 3.0,
    "sniper_first_buyers": 1.5,
    "funder_cluster": 2.5,
    "cross_token_cluster": 2.0,
    "shared_deployer_network": 2.5,
    "cross_token_funder": 2.0,
    "temporal_cluster": 2.0,
    "sniper_bot_pattern": 2.0,
    "mev_bundle": 2.5,
    "wash_trading_cluster": 2.5,
    "honeypot": 5.0,
    "mint_authority": 3.0,
    "freeze_authority": 2.0,
    "ownership_renounced": 2.0,
    "lp_locked": 3.0,
    "lp_liquidity_amount": 1.5,
    "buy_sell_tax": 1.5,
    "volume_authenticity": 1.5,
    "holder_overlap_scam": 2.0,
}

# ═══════════════════════════════════════════════════════════════════════════
# LOCAL COERCION HELPERS
# ═══════════════════════════════════════════════════════════════════════════

_MIDDOT = " \u00b7 "  # interpunct separator used in section one-liners
# ═══════════════════════════════════════════════════════════════════════════


def _f(value: Any) -> float | None:
    """Coerce to float; None for missing/NaN/blank values (never raises)."""
    return _float_or_none(value)


def _i(value: Any) -> int:
    """Coerce to int (0 when missing/unparseable)."""
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _sec(r: dict[str, Any]) -> dict[str, Any]:
    """Backend security evidence (GoPlus-style checks) or {}."""
    ev = r.get("evidence")
    s = ev.get("security") if isinstance(ev, dict) else None
    return s if isinstance(s, dict) else {}


def _sentinel_module(r: dict[str, Any], key: str) -> dict[str, Any]:
    """Pull one SENTINEL module result (free/premium buckets) defensively."""
    sent = r.get("sentinel")
    if not isinstance(sent, dict):
        return {}
    for bucket in ("free", "premium"):
        mods = sent.get(bucket)
        if isinstance(mods, dict):
            res = mods.get(key)
            if isinstance(res, dict):
                return res
    return {}


def _first_buyer_dict(r: dict[str, Any]) -> dict[str, Any]:
    """First-buyer entry from the own indexer engine, or {}.

    Accepts both payload shapes: the legacy list (``[{address, ...}]``) and
    the 250-wallet cohort dict (``{buyers: [...], aggregates: {...}, limit}``
    emitted by the deep endpoint / scanner engine). For the cohort shape the
    earliest buyer is the first ``buyers`` entry and the aggregates ride
    along so the RiskReport first-buyer section can report cohort depth.
    """
    fb = r.get("first_buyers")
    if isinstance(fb, list) and fb:
        first = fb[0]
        return first if isinstance(first, dict) else {}
    if isinstance(fb, dict):
        buyers = fb.get("buyers")
        if isinstance(buyers, list) and buyers:
            first = buyers[0]
            if isinstance(first, dict):
                return {
                    **first,
                    "cohort_size": (fb.get("aggregates") or {}).get("unique_buyers"),
                    "cohort_limit": fb.get("limit"),
                    "cohort_fresh_pct": (fb.get("aggregates") or {}).get("fresh_wallet_pct"),
                    "cohort_bundled_pct": (fb.get("aggregates") or {}).get("bundled_pct"),
                }
        return fb
    return {}


def _short(value: Any) -> str:
    """Shorten an address for display ("" for missing)."""
    if not value:
        return ""
    s = str(value)
    return s if len(s) <= 14 else f"{s[:10]}\u2026{s[-4:]}"


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


# ═══════════════════════════════════════════════════════════════════════════
# FACTOR CONSTRUCTION (mirrors risk_report._factor + _build_factors bands)
# ═══════════════════════════════════════════════════════════════════════════


def _factor(key: str, score: float | None, explanation: str) -> dict[str, Any]:
    """One RiskFactor dict (same shape as RiskReport.to_dict factors)."""
    if score is None:
        return {
            "key": key,
            "label": _FACTOR_LABELS.get(key, key),
            "risk_score": None,
            "status": "unknown",
            "explanation": explanation,
        }
    status = "critical" if score >= _CRITICAL_MIN else ("risk" if score >= _RISK_MIN else "ok")
    return {
        "key": key,
        "label": _FACTOR_LABELS.get(key, key),
        "risk_score": round(score, 1),
        "status": status,
        "explanation": explanation,
    }


def _build_factors_from_scan(r: dict[str, Any]) -> list[dict[str, Any]]:
    """The ~30 explainable factors from the scan payload (mirror of the
    scanner's ``_build_factors`` — same keys, labels, bands, explanations)."""
    deployer = _extract_deployer_section(r)
    top_25, _top25_pct = _top_25_list(r)
    holders = _extract_holders_section(r, deployer.get("address"))
    dev_team = _dev_team_share(top_25, deployer.get("address"))
    bundle = _extract_bundle_section(r)
    first_buyer = _extract_first_buyer_section(r, deployer.get("address"), bundle)
    honeypot = _extract_honeypot_section(r)
    liquidity = _extract_liquidity_section(r)
    fresh_pct = _fresh_wallet_pct(r)
    cex_pct = _cex_funded_pct(r)

    factors: list[dict[str, Any]] = []
    add = factors.append

    # ── Deployer (4) ──
    repeat = deployer.get("repeat_deployer")
    total = _i(deployer.get("total_deployments"))
    if repeat is None:
        add(_factor("deployer_repeat", None, "No deployer history data available."))
    else:
        add(
            _factor(
                "deployer_repeat",
                45.0 if repeat else 5.0,
                f"Deployer has launched {total} token(s); repeat deployers can recycle wallets across "
                "launches "
                + (
                    "(risky \u2014 same wallet, new token)."
                    if repeat
                    else "but shows no repeat-launch pattern."
                ),
            )
        )

    rugged = deployer.get("previously_rugged")
    bucket = deployer.get("risk_bucket")
    if rugged is None:
        add(_factor("deployer_rugged_before", None, "No prior-deployment outcome data available."))
    else:
        bucket_txt = f"bucket '{bucket}'" if bucket else "no fingerprint bucket"
        add(
            _factor(
                "deployer_rugged_before",
                80.0 if rugged else 10.0,
                f"Deployer {bucket_txt}: prior launches that died within 7 days "
                + (
                    "indicate a serial-rug pattern."
                    if rugged
                    else "survived past the 7-day window."
                ),
            )
        )

    fresh_wallet = deployer.get("fresh_wallet")
    if fresh_wallet is None:
        add(_factor("deployer_new_wallet", None, "Deployer wallet age unknown."))
    else:
        add(
            _factor(
                "deployer_new_wallet",
                40.0 if fresh_wallet else 5.0,
                "Deployer wallet is fresh (<30 days) \u2014 recent first activity on a new wallet is a "
                "farm signal."
                if fresh_wallet
                else "Deployer wallet is established.",
            )
        )

    funder = deployer.get("funder") or {}
    if funder.get("address"):
        is_cex = bool(funder.get("is_cex"))
        is_fresh = bool(funder.get("is_fresh"))
        score = 10.0 if is_cex else (60.0 if is_fresh else 30.0)
        add(
            _factor(
                "deployer_funding",
                score,
                f"Deployer funded by {'CEX ' + str(funder.get('cex_label')) if is_cex else 'a non-exchange wallet'} "
                + ("(fresh funder with <10 txs)." if is_fresh and not is_cex else "."),
            )
        )
    else:
        add(_factor("deployer_funding", None, "Deployer funding source unknown."))

    # ── Holders (5) ──
    top10 = _f(holders.get("top_10_pct"))
    if top10 is None:
        add(_factor("holder_concentration", None, "Top-10 holder share unavailable."))
    else:
        score = 0.0 if top10 < 30 else (35.0 if top10 < 50 else (65.0 if top10 < 80 else 90.0))
        add(
            _factor(
                "holder_concentration",
                score,
                f"Top-10 holders control {top10:.1f}% of tracked supply \u2014 "
                + (
                    "distributed."
                    if score == 0
                    else "a rug/exit-liquidity risk if they coordinate."
                ),
            )
        )

    if dev_team is None:
        add(_factor("dev_team_share", None, "Dev/team wallet share unavailable."))
    else:
        add(
            _factor(
                "dev_team_share",
                0.0 if dev_team < 10 else (50.0 if dev_team < 25 else 85.0),
                f"Deployer + team wallets hold {dev_team:.1f}% \u2014 "
                + (
                    "large insider share enables coordinated dumps."
                    if dev_team >= 25
                    else "moderate insider presence."
                ),
            )
        )

    whale_pct = top_25[0].get("share_pct") if top_25 else None
    whale = _f(whale_pct)
    if whale is None:
        add(_factor("top_holder_whale", None, "Top holder share unavailable."))
    else:
        add(
            _factor(
                "top_holder_whale",
                0.0 if whale < 15 else (55.0 if whale < 30 else 85.0),
                f"Largest holder controls {whale:.1f}% \u2014 "
                + ("normal." if whale < 15 else "a single wallet selling is a price-crash risk."),
            )
        )

    if fresh_pct is None:
        add(_factor("fresh_wallet_pct", None, "Fresh-wallet % unavailable."))
    else:
        score = 0.0 if fresh_pct < 20 else (40.0 if fresh_pct < 40 else 75.0)
        add(
            _factor(
                "fresh_wallet_pct",
                score,
                f"{fresh_pct:.1f}% of analyzed holders are fresh wallets (<30 days) \u2014 "
                + (
                    "a bot-farm / airdrop-farming concentration signal."
                    if score >= 40
                    else "within normal bounds."
                ),
            )
        )

    if cex_pct is None:
        add(_factor("cex_funded_wallet_pct", None, "CEX-funded wallet % unavailable."))
    else:
        score = 15.0 if cex_pct >= 40 else (40.0 if cex_pct < 10 else 20.0)
        add(
            _factor(
                "cex_funded_wallet_pct",
                score,
                f"{cex_pct:.1f}% of holders were funded by CEX wallets \u2014 "
                + (
                    "institutional backing."
                    if score <= 15
                    else "low exchange backing leaves the holder base unvetted."
                ),
            )
        )

    # ── Launch (4) ──
    fb_addr = first_buyer.get("address")
    if not fb_addr:
        add(_factor("first_buyer_deployer", None, "First-buyer data unavailable."))
    else:
        is_deployer = bool(first_buyer.get("is_deployer"))
        add(
            _factor(
                "first_buyer_deployer",
                70.0 if is_deployer else 5.0,
                f"First buyer is {'the deployer' if is_deployer else 'not the deployer'} "
                f"({_short(fb_addr)}).",
            )
        )

    in_bundle = first_buyer.get("in_bundle")
    if in_bundle is None:
        add(_factor("first_buyer_bundle", None, "First-buyer bundle membership unknown."))
    else:
        add(
            _factor(
                "first_buyer_bundle",
                70.0 if in_bundle else 5.0,
                "First buyer entered in the same launch cluster as other wallets \u2014 "
                + ("classic bundle-buy opening." if in_bundle else "bought independently."),
            )
        )

    is_bundled = bundle.get("is_bundled")
    if is_bundled is None:
        add(_factor("bundle_launch", None, "Bundle-launch data unavailable."))
    else:
        launch_score = _f(bundle.get("launch_cluster_score")) or 0.0
        score = 80.0 if is_bundled else (45.0 if launch_score >= 50 else 5.0)
        explanation = (
            "Launch bundle DETECTED (coordinated buys at deployment) \u2014 "
            "a top rug vector where the same group controls the opening supply."
            if is_bundled
            else f"Launch bundle not detected (launch cluster score {launch_score:.0f}/100)."
        )
        add(_factor("bundle_launch", score, explanation))

    snipers = bundle.get("sniper_first_buyers")
    if snipers is None:
        add(_factor("sniper_first_buyers", None, "Sniper first-buyer data unavailable."))
    else:
        n = _i(snipers)
        score = 0.0 if n == 0 else (40.0 if n < 5 else 70.0)
        add(
            _factor(
                "sniper_first_buyers",
                score,
                f"{n} sniper first-buyers in launch clusters \u2014 "
                + (
                    "dev-controlled or MEV bots opening positions."
                    if score >= 40
                    else "no meaningful sniper presence."
                ),
            )
        )

    # ── Cluster (8) — the bot payload has no risk_cluster engine output, so
    #    the beyond-market cluster factors stay UNKNOWN (honest, fail-open).
    #    A backend-provided RiskReport dict carries real cluster data. ──
    for key in (
        "funder_cluster",
        "cross_token_cluster",
        "shared_deployer_network",
        "cross_token_funder",
        "temporal_cluster",
        "sniper_bot_pattern",
        "mev_bundle",
        "wash_trading_cluster",
    ):
        add(_factor(key, None, f"{_FACTOR_LABELS[key]} data unavailable on this scan tier."))

    # ── Honeypot / contract (4) ──
    hp = honeypot.get("is_honeypot")
    if hp is None:
        add(_factor("honeypot", None, "Honeypot status unknown (no simulation data)."))
    else:
        add(
            _factor(
                "honeypot",
                95.0 if hp else 0.0,
                "Honeypot DETECTED \u2014 the token can be bought but not sold; holders cannot exit, "
                "a definitive scam vector."
                if hp
                else "Sell-side simulation passes \u2014 not a honeypot.",
            )
        )

    mintable = honeypot.get("mintable")
    if mintable is None:
        add(_factor("mint_authority", None, "Mint authority unknown."))
    else:
        add(
            _factor(
                "mint_authority",
                85.0 if mintable else 5.0,
                "Mint authority is ACTIVE \u2014 the deployer can mint unlimited supply and dump on "
                "holders."
                if mintable
                else "Mint authority renounced \u2014 supply cannot be inflated.",
            )
        )

    freezable = honeypot.get("freezable")
    if freezable is None:
        add(_factor("freeze_authority", None, "Freeze authority unknown."))
    else:
        add(
            _factor(
                "freeze_authority",
                70.0 if freezable else 5.0,
                "Freeze authority is ACTIVE \u2014 holders can be frozen out of their positions."
                if freezable
                else "Freeze authority renounced.",
            )
        )

    renounced = honeypot.get("ownership_renounced")
    if renounced is None:
        add(_factor("ownership_renounced", None, "Ownership renunciation unknown."))
    else:
        add(
            _factor(
                "ownership_renounced",
                5.0 if renounced else 50.0,
                "Ownership renounced \u2014 contract owner cannot change rules."
                if renounced
                else "Ownership NOT renounced \u2014 the owner retains control (tax changes, blacklist, "
                "upgrade).",
            )
        )

    # ── Liquidity / tax / market (5) ──
    locked = _f(liquidity.get("locked_pct"))
    if locked is None:
        add(_factor("lp_locked", None, "LP lock status unavailable."))
    else:
        add(
            _factor(
                "lp_locked",
                0.0 if locked >= 50 else (55.0 if locked >= 20 else 85.0),
                f"{locked:.1f}% of LP is locked/burned \u2014 "
                + (
                    "liquidity cannot be pulled."
                    if locked >= 50
                    else "unlocked LP is a rug vector (dev can remove it)."
                ),
            )
        )

    liq_usd = _f(liquidity.get("liquidity_usd"))
    if liq_usd is None:
        add(_factor("lp_liquidity_amount", None, "Liquidity amount unavailable."))
    else:
        add(
            _factor(
                "lp_liquidity_amount",
                0.0 if liq_usd >= 100_000 else (45.0 if liq_usd >= 20_000 else 75.0),
                f"Liquidity is ~${liq_usd:,.0f} \u2014 "
                + (
                    "deep enough to absorb sells."
                    if liq_usd >= 100_000
                    else "thin liquidity amplifies exit-sell price impact."
                ),
            )
        )

    buy_tax = _f(honeypot.get("buy_tax"))
    sell_tax = _f(honeypot.get("sell_tax"))
    if buy_tax is None and sell_tax is None:
        add(_factor("buy_sell_tax", None, "Tax data unavailable."))
    else:
        b = buy_tax or 0.0
        s = sell_tax or 0.0
        combined = b + s
        score = 0.0 if combined < 10 else (40.0 if combined < 25 else 80.0)
        add(
            _factor(
                "buy_sell_tax",
                score,
                f"Buy tax {b:.1f}% / sell tax {s:.1f}% \u2014 "
                + (
                    "combined tax is a profit-trap for holders."
                    if score >= 40
                    else "taxes within normal bounds."
                ),
            )
        )

    fake_pct = _fake_volume_pct(r)
    if fake_pct is None:
        add(_factor("volume_authenticity", None, "Volume authenticity unavailable."))
    else:
        add(
            _factor(
                "volume_authenticity",
                0.0 if fake_pct < 10 else (50.0 if fake_pct < 50 else 85.0),
                f"{fake_pct:.1f}% of volume is estimated fake \u2014 "
                + (
                    "washed volume creates a false liquidity/momentum picture."
                    if fake_pct >= 50
                    else "volume looks organic."
                ),
            )
        )

    overlap = r.get("holder_overlap")
    if not isinstance(overlap, dict):
        overlap = {}
    max_overlap = _f(overlap.get("max_overlap_pct"))
    worst_scam = bool(overlap.get("worst_token_is_scam"))
    if max_overlap is None:
        max_overlap = _f(r.get("holder_overlap_pct"))
    if max_overlap is None:
        add(_factor("holder_overlap_scam", None, "Holder-overlap-vs-scams data unavailable."))
    else:
        score = 0.0 if max_overlap < 30 else (60.0 if not worst_scam else 85.0)
        add(
            _factor(
                "holder_overlap_scam",
                score,
                f"{max_overlap:.1f}% holder overlap with known scam tokens"
                + (
                    " \u2014 the same wallets appear in confirmed scams."
                    if worst_scam
                    else " \u2014 elevated cross-holding."
                ),
            )
        )

    return factors


# ═══════════════════════════════════════════════════════════════════════════
# COMPOSITE + DETERMINATION (mirrors risk_report._composite_and_determination)
# ═══════════════════════════════════════════════════════════════════════════


def _composite_and_determination(
    factors: list[dict[str, Any]],
    honeypot: dict[str, Any],
    deployer: dict[str, Any],
    bundle: dict[str, Any],
) -> tuple[int | None, str, str]:
    """Weighted-average composite over KNOWN factors + high-level verdict."""
    known = [f for f in factors if f.get("risk_score") is not None]
    if not known:
        return None, "unknown", "Insufficient data to score this token."

    total_w = 0.0
    total = 0.0
    for f in known:
        w = _FACTOR_WEIGHTS.get(f["key"], 1.0)
        total += float(f["risk_score"]) * w
        total_w += w
    composite = round(total / total_w) if total_w > 0 else 50

    determination: str
    if bool(honeypot.get("is_honeypot")) or composite >= _SCAM_MIN:
        determination = "scam"
    elif composite >= _RUG_MIN or (
        len(known) >= _MIN_KNOWN_FOR_DETERMINATION
        and bool(deployer.get("previously_rugged"))
        and bool(bundle.get("is_bundled"))
    ):
        determination = "rug"
    elif composite >= _RISKY_MIN:
        determination = "risky"
    elif composite < _RISKY_MIN:
        determination = "ok"
    else:
        determination = "unknown"

    summary = _summary_text(determination, composite, len(known), len(factors))
    return composite, determination, summary


def _summary_text(determination: str, composite: int | None, known: int, total: int) -> str:
    base = f"Composite risk {composite}/100 from {known}/{total} known factors."
    if determination == "scam":
        return f"{base} DETERMINATION: SCAM \u2014 decisive scam vectors detected; do not trade."
    if determination == "rug":
        return f"{base} DETERMINATION: RUG \u2014 strong rug-pull signals; extreme caution."
    if determination == "risky":
        return f"{base} DETERMINATION: RISKY \u2014 elevated risk; only trade with full awareness."
    if determination == "ok":
        return f"{base} DETERMINATION: OK \u2014 no dominant scam/rug signal detected."
    return f"{base} Determination: UNKNOWN \u2014 insufficient data."


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC BUILDER — the same dict shape as RiskReport.to_dict()
# ═══════════════════════════════════════════════════════════════════════════


def _build_risk_report_from_scan(r: dict[str, Any], *, deep: bool = False) -> dict[str, Any] | None:
    """Synthesize the RiskReport dict from the bot's scan payload (pure).

    Returns None only when the scan carries no data at all (no address) —
    the renderer then omits the section instead of showing an empty block.
    """
    address = r.get("address") or r.get("token") or ""
    if not address:
        return None

    deployer = _extract_deployer_section(r)
    top_25, top25_pct = _top_25_list(r)
    holders = _extract_holders_section(r, deployer.get("address"))
    holders["top_25_pct"] = top25_pct
    holders["dev_team_share"] = _dev_team_share(top_25, deployer.get("address"))
    bundle = _extract_bundle_section(r)
    first_buyer = _extract_first_buyer_section(r, deployer.get("address"), bundle)
    honeypot = _extract_honeypot_section(r)
    liquidity = _extract_liquidity_section(r)
    fresh_pct = _fresh_wallet_pct(r)
    cex_pct = _cex_funded_pct(r)

    factors = _build_factors_from_scan(r)
    composite, determination, summary = _composite_and_determination(
        factors, honeypot, deployer, bundle
    )

    known = [f for f in factors if f.get("risk_score") is not None]
    unknown = [f for f in factors if f.get("risk_score") is None]
    critical = [f for f in known if f.get("status") == "critical"]
    risk = [f for f in known if f.get("status") == "risk"]

    sources_used: list[str] = []
    if r.get("evidence"):
        sources_used.append("security")
    if r.get("sentinel"):
        sources_used.append("sentinel")
    if r.get("bundle_analysis"):
        sources_used.append("bundle_analysis")
    if r.get("wallet_metrics") or r.get("funding_analysis"):
        sources_used.append("funding_analysis")
    if r.get("holder_overlap") or r.get("holder_overlap_pct") is not None:
        sources_used.append("holder_overlap")
    if r.get("top_holders"):
        sources_used.append("holders")

    data_quality = {
        "factors_total": len(factors),
        "factors_known": len(known),
        "factors_unknown": len(unknown),
        "critical_factors": len(critical),
        "risk_factors": len(risk),
        "ok_factors": len([f for f in known if f.get("status") == "ok"]),
        "coverage_pct": round(len(known) / len(factors) * 100, 1) if factors else 0.0,
    }

    return {
        "token_address": address,
        "chain": str(r.get("chain") or ""),
        "scanned_at": str(r.get("scanned_at") or ""),
        "tier": str(r.get("tier") or ""),
        "deployer": deployer,
        "holders": holders,
        "first_buyer": first_buyer,
        "top_25_holders": top_25,
        "fresh_wallet_pct": fresh_pct,
        "cex_funded_wallet_pct": cex_pct,
        "bundle": bundle,
        "cluster": {
            "funder_tree": [],
            "shared_deployer_network": [],
            "cross_token_clusters": [],
            "cross_token_funders": [],
            "temporal_clusters": [],
            "sniper_patterns": [],
            "mev_bundles": [],
            "wash_trading": {},
            "cluster_risk_score": None,
            "cluster_risk_label": "unknown",
            "description": "",
        },
        "honeypot": honeypot,
        "liquidity": liquidity,
        "factors": factors,
        "composite_score": composite,
        "determination": determination,
        "summary": summary,
        "deeper_analysis": _build_deeper_analysis(
            deployer, holders, first_buyer, bundle, honeypot, liquidity, factors, deep=deep
        ),
        "ai_assisted": False,
        "data_quality": data_quality,
        "sources_used": sources_used,
    }


def _build_deeper_analysis(
    deployer: dict[str, Any],
    holders: dict[str, Any],
    first_buyer: dict[str, Any],
    bundle: dict[str, Any],
    honeypot: dict[str, Any],
    liquidity: dict[str, Any],
    factors: list[dict[str, Any]],
    *,
    deep: bool = False,
) -> dict[str, Any]:
    """Per-section narrative (mirror of risk_report._build_deeper_analysis)."""
    factor_map = {f["key"]: f for f in factors}

    def _line(key: str) -> str:
        f = factor_map.get(key)
        if f is None:
            return ""
        return f"- {f['label']}: {f['explanation']}"

    fb_cohort = ""
    if first_buyer.get("cohort_size") is not None:
        cohort = f"{first_buyer['cohort_size']} wallets"
        if first_buyer.get("cohort_limit") is not None:
            cohort += f" (limit {first_buyer['cohort_limit']})"
        extra = []
        if first_buyer.get("cohort_fresh_pct") is not None:
            extra.append(f"{first_buyer['cohort_fresh_pct']}% fresh")
        if first_buyer.get("cohort_bundled_pct") is not None:
            extra.append(f"{first_buyer['cohort_bundled_pct']}% bundled")
        fb_cohort = f" Cohort: {cohort}" + (f" ({', '.join(extra)})" if extra else "")

    cluster_unavailable = (
        "not available on this scan tier (see /deepscan)"
        if not deep
        else "unavailable — the beyond-market cluster engines (risk_cluster) reported no data"
    )

    return {
        "deployer": {
            "summary": (
                f"Deployer {_short(deployer.get('address') or 'unknown')} has "
                f"{deployer.get('total_deployments', '?')} deployment(s); "
                f"repeat={deployer.get('repeat_deployer')}, "
                f"previously_rugged={deployer.get('previously_rugged')}."
            ),
            "factors": [
                ln
                for ln in [
                    _line("deployer_repeat"),
                    _line("deployer_rugged_before"),
                    _line("deployer_new_wallet"),
                    _line("deployer_funding"),
                ]
                if ln
            ],
        },
        "holders": {
            "summary": (
                f"{holders.get('total_holders', '?')} holders; top-10 share "
                f"{holders.get('top_10_pct', '?')}%; concentration {holders.get('concentration', 'unknown')}."
            ),
            "factors": [
                ln
                for ln in [
                    _line("holder_concentration"),
                    _line("dev_team_share"),
                    _line("top_holder_whale"),
                    _line("fresh_wallet_pct"),
                    _line("cex_funded_wallet_pct"),
                ]
                if ln
            ],
        },
        "first_buyer": {
            "summary": (
                f"First buyer {_short(first_buyer.get('address') or 'unknown')} \u2014 "
                f"deployer={first_buyer.get('is_deployer')}, in_bundle={first_buyer.get('in_bundle')}."
                f"{fb_cohort}"
            ),
            "factors": [
                ln for ln in [_line("first_buyer_deployer"), _line("first_buyer_bundle")] if ln
            ],
        },
        "launch": {
            "summary": (
                f"Launch bundle={bundle.get('is_bundled')} (cluster score "
                f"{bundle.get('launch_cluster_score') or 0}/100, deployer-funded "
                f"{bundle.get('deployer_funded_pct') or 0}%, first-time buyers "
                f"{bundle.get('first_time_buyer_pct') or 0}%)."
            ),
            "factors": [ln for ln in [_line("bundle_launch"), _line("sniper_first_buyers")] if ln],
        },
        "cluster": {
            "summary": (
                "Cluster graph: funder tree + shared-deployer networks + cross-token "
                f"clusters \u2014 {cluster_unavailable}."
            ),
            "factors": [
                ln
                for ln in [
                    _line("funder_cluster"),
                    _line("shared_deployer_network"),
                    _line("cross_token_cluster"),
                    _line("cross_token_funder"),
                    _line("temporal_cluster"),
                    _line("sniper_bot_pattern"),
                    _line("mev_bundle"),
                    _line("wash_trading_cluster"),
                ]
                if ln
            ],
        },
        "honeypot_contract": {
            "summary": (
                f"Honeypot={honeypot.get('is_honeypot')}; buy tax {honeypot.get('buy_tax') or 0}%, "
                f"sell tax {honeypot.get('sell_tax') or 0}%; mintable={honeypot.get('mintable')}, "
                f"freezable={honeypot.get('freezable')}, "
                f"ownership_renounced={honeypot.get('ownership_renounced')}."
            ),
            "factors": [
                ln
                for ln in [
                    _line("honeypot"),
                    _line("mint_authority"),
                    _line("freeze_authority"),
                    _line("ownership_renounced"),
                ]
                if ln
            ],
        },
        "liquidity_market": {
            "summary": (
                f"LP locked {liquidity.get('locked_pct') or 0}% (locked={liquidity.get('is_locked')}); "
                f"liquidity ~${liquidity.get('liquidity_usd') or 0:,.0f}."
            ),
            "factors": [
                ln
                for ln in [
                    _line("lp_locked"),
                    _line("lp_liquidity_amount"),
                    _line("buy_sell_tax"),
                    _line("volume_authenticity"),
                    _line("holder_overlap_scam"),
                ]
                if ln
            ],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# RENDERER (Telegram HTML)
# ═══════════════════════════════════════════════════════════════════════════

_DETERMINATION_EMOJI = {
    "scam": "\U0001f6a8",
    "rug": "\U0001f534",
    "risky": "\U0001f7e0",
    "ok": "\U0001f7e2",
    "unknown": "\u26aa",
}

_STATUS_EMOJI = {
    "critical": "\U0001f534",
    "risk": "\U0001f7e1",
    "ok": "\U0001f7e2",
    "unknown": "\u26aa",
}


#: Section one-liners for the concise renderer (key → (emoji, extractor)).
def _section_lines(rep: dict[str, Any]) -> list[str]:
    """The full-product one-liners: deployer / holders / first buyer / top-25 /
    fresh% / CEX% / bundle / cluster / honeypot / liquidity."""
    lines: list[str] = []

    dep = rep.get("deployer") or {}
    d_bits: list[str] = []
    if dep.get("address"):
        d_bits.append(f"<code>{html.escape(_short(dep['address']))}</code>")
    if dep.get("total_deployments"):
        d_bits.append(f"{dep['total_deployments']} tokens")
    if dep.get("repeat_deployer") is not None:
        d_bits.append("repeat=" + ("\u2713" if dep["repeat_deployer"] else "\u2717"))
    if dep.get("previously_rugged") is not None:
        d_bits.append("rugged_before=" + ("\u2713" if dep["previously_rugged"] else "\u2717"))
    if d_bits:
        lines.append(f"  \U0001f468\u200d\U0001f4bb <b>Deployer:</b> {_MIDDOT.join(d_bits)}")

    hol = rep.get("holders") or {}
    h_bits: list[str] = []
    if hol.get("total_holders") is not None:
        h_bits.append(f"{hol['total_holders']:,} holders")
    if hol.get("top_10_pct") is not None:
        h_bits.append(f"top-10 {hol['top_10_pct']}%")
    fresh = rep.get("fresh_wallet_pct")
    if fresh is not None:
        h_bits.append(f"fresh {fresh:.0f}%")
    cex = rep.get("cex_funded_wallet_pct")
    if cex is not None:
        h_bits.append(f"CEX-funded {cex:.0f}%")
    if h_bits:
        lines.append(f"  \U0001f465 <b>Holders:</b> {_MIDDOT.join(h_bits)}")

    fb = rep.get("first_buyer") or {}
    if fb.get("address"):
        fb_bits = [f"<code>{html.escape(_short(fb['address']))}</code>"]
        if fb.get("is_deployer") is not None:
            fb_bits.append("deployer=" + ("\u2713" if fb["is_deployer"] else "\u2717"))
        if fb.get("in_bundle") is not None:
            fb_bits.append("in_bundle=" + ("\u2713" if fb["in_bundle"] else "\u2717"))
        if fb.get("cohort_size") is not None:
            fb_bits.append(f"cohort {fb['cohort_size']}")
        lines.append(f"  \U0001f6d2 <b>First buyer:</b> {_MIDDOT.join(fb_bits)}")

    top25_pct = hol.get("top_25_pct")
    if top25_pct is not None:
        lines.append(f"  \U0001f3c6 <b>Top-25:</b> {top25_pct:.1f}% of tracked supply")

    bun = rep.get("bundle") or {}
    if bun.get("is_bundled") is not None:
        b_str = "\U0001f6a8 DETECTED" if bun["is_bundled"] else "not detected"
        if bun.get("launch_cluster_score") is not None:
            b_str += f" (cluster {bun['launch_cluster_score']:.0f}/100)"
        lines.append(f"  \U0001f578\ufe0f <b>Bundle:</b> {b_str}")

    cl = rep.get("cluster") or {}
    if isinstance(cl, dict) and cl.get("cluster_risk_score") is not None:
        lines.append(
            f"  \U0001f50e <b>Cluster:</b> {cl['cluster_risk_score']}/100 "
            f"({html.escape(str(cl.get('cluster_risk_label') or 'unknown').upper())})"
        )

    hp = rep.get("honeypot") or {}
    hp_bits: list[str] = []
    if hp.get("is_honeypot") is not None:
        hp_bits.append("honeypot=" + ("\U0001f6a8 YES" if hp["is_honeypot"] else "\u2717"))
    if hp.get("mintable") is not None:
        hp_bits.append("mint=" + ("\u2713" if hp["mintable"] else "\u2717"))
    if hp.get("freezable") is not None:
        hp_bits.append("freeze=" + ("\u2713" if hp["freezable"] else "\u2717"))
    if hp.get("buy_tax") is not None or hp.get("sell_tax") is not None:
        hp_bits.append(f"tax {hp.get('buy_tax') or 0:.0f}/{hp.get('sell_tax') or 0:.0f}%")
    if hp_bits:
        lines.append(f"  \U0001f6e1\ufe0f <b>Honeypot:</b> {_MIDDOT.join(hp_bits)}")

    liq = rep.get("liquidity") or {}
    liq_bits: list[str] = []
    if liq.get("locked_pct") is not None:
        liq_bits.append(f"locked {liq['locked_pct']:.0f}%")
    if liq.get("liquidity_usd") is not None:
        liq_bits.append(f"${liq['liquidity_usd']:,.0f}")
    if liq_bits:
        lines.append(f"  \U0001f4a7 <b>Liquidity:</b> {_MIDDOT.join(liq_bits)}")

    return lines


def _headline_lines(rep: dict[str, Any]) -> list[str]:
    """Composite score + determination + coverage header lines."""
    composite = rep.get("composite_score")
    determination = str(rep.get("determination") or "unknown")
    emoji = _DETERMINATION_EMOJI.get(determination, "\u26aa")
    comp_str = f"<b>{composite}/100</b>" if composite is not None else "<b>unknown</b>"
    dq = rep.get("data_quality") or {}
    coverage = (
        f"{dq.get('factors_known', 0)}/{dq.get('factors_total', 0)} factors known "
        f"({dq.get('coverage_pct', 0.0)}%)"
    )
    lines = [
        f"  \U0001f3af <b>Composite Risk:</b> {comp_str} \u00b7 {emoji} <b>{html.escape(determination.upper())}</b>",
        f"  \U0001f4ca {coverage}",
    ]
    if rep.get("summary"):
        lines.append(f"  \U0001f4a1 {html.escape(_clip(rep['summary'], 220))}")
    return lines


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def _top_factor_lines(rep: dict[str, Any], limit: int = 4) -> list[str]:
    """Top critical/risk factor bullets (basic scans; empty when none fired)."""
    factors = rep.get("factors") or []
    bad = [
        f
        for f in factors
        if isinstance(f, dict)
        and f.get("status") in ("critical", "risk")
        and f.get("risk_score") is not None
    ]
    if not bad:
        return []
    lines: list[str] = []
    for f in bad[:limit]:
        status = f.get("status")
        emoji = _STATUS_EMOJI.get(str(status), "\u26aa")
        score = f.get("risk_score")
        lines.append(
            f"  {emoji} {html.escape(str(f.get('label') or f.get('key')))} "
            f"<b>{score:.0f}/100</b> \u2014 {html.escape(_clip(str(f.get('explanation') or ''), 160))}"
        )
    return lines


def _factor_table_lines(rep: dict[str, Any]) -> list[str]:
    """Full ~30-factor table (deep scans)."""
    factors = rep.get("factors") or []
    if not factors:
        return ["  \u26aa <i>factor table unavailable</i>"]
    lines: list[str] = []
    for f in factors:
        if not isinstance(f, dict):
            continue
        status = str(f.get("status") or "unknown")
        emoji = _STATUS_EMOJI.get(status, "\u26aa")
        label = html.escape(str(f.get("label") or f.get("key") or "unknown"))
        score = f.get("risk_score")
        score_str = f"<b>{score:.0f}/100</b>" if score is not None else "\u2014"
        lines.append(f"  {emoji} {label:<36} {score_str:>8}")
    return lines


def _deeper_lines(rep: dict[str, Any]) -> list[str]:
    """Deeper-level per-section narrative (deep scans)."""
    deeper = rep.get("deeper_analysis")
    if not isinstance(deeper, dict) or not deeper:
        return []
    lines: list[str] = []
    for section, payload in deeper.items():
        if not isinstance(payload, dict):
            continue
        summary = str(payload.get("summary") or "").strip()
        if not summary:
            continue
        lines.append(f"  \u25ba {html.escape(str(section).upper())}")
        lines.append(f"      {html.escape(_clip(summary, 300))}")
        for ln in payload.get("factors") or []:
            if isinstance(ln, str) and ln.strip():
                lines.append(f"      {html.escape(_clip(ln.strip(), 240))}")
    return lines


def _render_risk_report(rep: dict[str, Any], *, deep: bool) -> list[str]:
    """Render the RiskReport dict as Telegram-HTML lines."""
    lines = _headline_lines(rep)
    lines.append("")
    lines.extend(_section_lines(rep))
    if deep:
        table = _factor_table_lines(rep)
        if table:
            lines.extend(["", f"  \U0001f4ca <b>Factors ({len(rep.get('factors') or [])})</b>"])
            lines.extend(table)
        deeper = _deeper_lines(rep)
        if deeper:
            lines.extend(["", "  \U0001f9e0 <b>Deeper Analysis</b>"])
            lines.extend(deeper)
        if rep.get("ai_assisted"):
            lines.append("  \U0001f916 <i>analysis AI-assisted</i>")
    else:
        top = _top_factor_lines(rep)
        if top:
            lines.extend(["", "  \u26a0\ufe0f <b>Top Risks</b>"])
            lines.extend(top)
    return lines


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════════


def format_risk_report_block(r: dict[str, Any], *, deep: bool = False) -> list[str]:
    """Risk Report section for the scan report ([] when the scan has no data).

    Uses the backend-provided RiskReport dict when the payload carries one;
    otherwise builds the same shape from the scan fields (see
    ``_build_risk_report_from_scan``). Basic scans render the concise block;
    deep scans render the full ~30-factor table + deeper analysis.
    """
    raw = r.get("risk_report")
    rep = raw if isinstance(raw, dict) else _build_risk_report_from_scan(r, deep=deep)
    if not isinstance(rep, dict) or not rep.get("factors"):
        return []
    # AI-assisted marker: when the deep scan ran the LLM verdict pass
    # (``ai_verdict``), flag the locally-built report so the renderer notes
    # the deeper explanation is AI-assisted (backend reports carry their own
    # ``ai_assisted`` flag).
    if deep and not rep.get("ai_assisted") and isinstance(r.get("ai_verdict"), str):
        rep = {**rep, "ai_assisted": True}
    title = "Risk Report \u2014 30-factor breakdown" if deep else "Risk Report"
    return ["", f"\U0001f3af <b>{title}</b>", thin_sep(), *_render_risk_report(rep, deep=deep)]


__all__ = [
    "_build_risk_report_from_scan",
    "format_risk_report_block",
]
