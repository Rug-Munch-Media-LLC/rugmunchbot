"""Factor construction for the RiskReport (mirrors risk_report._factor).

Split out of ``formatters/_scan_report_riskreport.py`` during the de-godding
pass. ``_build_factors_from_scan`` synthesizes the ~30 explainable factors
from the scan payload — same keys, labels, bands and explanations as the
canonical scanner module. Pure module: no network, no I/O.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._scan_report_common._fields import (
    _cex_funded_pct,
    _fake_volume_pct,
    _fresh_wallet_pct,
)
from rugmunchbot.formatters._scan_report_riskreport._constants import (
    _CRITICAL_MIN,
    _FACTOR_LABELS,
    _RISK_MIN,
    _f,
    _i,
    _short,
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
