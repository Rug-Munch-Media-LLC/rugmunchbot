"""Public RiskReport builder — the same dict shape as RiskReport.to_dict().

Split out of ``formatters/_scan_report_riskreport.py`` during the de-godding
pass. ``_build_risk_report_from_scan`` synthesizes the RiskReport dict from
the bot's scan payload (pure); ``_build_deeper_analysis`` produces the
per-section narrative. Pure module: no network, no I/O.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._scan_report_common._fields import (
    _cex_funded_pct,
    _fresh_wallet_pct,
)
from rugmunchbot.formatters._scan_report_riskreport._constants import _short
from rugmunchbot.formatters._scan_report_riskreport._determination import (
    _composite_and_determination,
)
from rugmunchbot.formatters._scan_report_riskreport._extract import (
    _dev_team_share,
    _extract_bundle_section,
    _extract_deployer_section,
    _extract_first_buyer_section,
    _extract_holders_section,
    _extract_honeypot_section,
    _extract_liquidity_section,
    _top_25_list,
)
from rugmunchbot.formatters._scan_report_riskreport._factors import _build_factors_from_scan

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
        # 2026-09-02 scans-channel fix: pass the new scanner engines through
        # so the channel card renders dev_sold / dex_paid / ATH / snipers /
        # gini-HHI / first-tx% / mint-freeze authority exactly like the /scan
        # report does (``_engine_metrics_rows``). Only dict payloads pass —
        # absent or "unavailable" engines render nothing (honest-data rule).
        **{
            key: r[key]
            for key in (
                "dev_sold",
                "dex_paid",
                "price_metrics",
                "sniper_count",
                "holder_concentration",
                "wallet_metrics",
                "contract_authority",
            )
            if isinstance(r.get(key), dict)
        },
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
