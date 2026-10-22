"""Signal assembly for the verdict-first scan card.

Rules 1, 4, 6, 7, 8 and 10 live here: verdict inputs are assembled as
independent dimensions (never averaged), the dev verdict is rug-rate +
funder, wash recycling is labelled instead of reported as demand, KOL shows
matched / registry size, the market block is built last, and a thin payload
is flagged so the verdict can say ``insufficient evidence``.

One public entry point: :func:`collect`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rugmunchbot.formatters._scan_report_common import _fmt_market_value
from rugmunchbot.formatters._scan_report_common._fields import _mint_auth
from rugmunchbot.formatters._vcard_clusters import cluster_lines
from rugmunchbot.formatters._vcard_fields import DASH, source_for
from rugmunchbot.formatters._vcard_safety import safety_row


@dataclass
class Signals:
    """Everything the card needs, already decided (renderer picks layout)."""

    safety: str
    hard_fail: bool
    clusters: list[str] = field(default_factory=list)
    dev: str = ""
    kol: str = ""
    wash: str = ""
    market: str = ""
    top10: str | None = None
    has_evidence: bool = False

def _dev(r: dict[str, Any], ts: str) -> str:
    """Rule 4: rug-rate + funder, never a launch count."""
    profile = r.get("deployer_profile") if isinstance(r.get("deployer_profile"), dict) else {}
    evidence = (
        (r.get("evidence") or {}).get("deployer") if isinstance(r.get("evidence"), dict) else {}
    )
    evidence = evidence if isinstance(evidence, dict) else {}
    funder = (
        evidence.get("funder_profile") if isinstance(evidence.get("funder_profile"), dict) else {}
    )
    rate = profile.get("rug_rate", evidence.get("rug_rate"))
    total = profile.get("total_deployments", evidence.get("total_deployments"))
    bits: list[str] = []
    try:
        rate_f = float(rate)
        total_i = int(total) if isinstance(total, (int, float)) else None
        rugs = round(rate_f * total_i) if total_i else None
        bits.append(
            f"rug-rate {rate_f * 100:.0f}%" + (f" ({rugs}/{total_i})" if rugs is not None else "")
        )
    except (TypeError, ValueError):
        bits.append(f"rug-rate {DASH} (no deployer history)")
    funding = str(funder.get("funding_type") or "").strip()
    if funding:
        bits.append(f"funder {funding}{' (CEX)' if funder.get('is_cex') else ''}")
    elif funder.get("address"):
        bits.append(f"funder {str(funder['address'])[:10]}\u2026")
    else:
        bits.append(f"funder {DASH} (not resolved)")
    joined = " \u00b7 ".join(bits)
    return f"Dev       {joined} \u00b7 {source_for(r, 'deployer')} \u00b7 {ts}"


def _kol(r: dict[str, Any], ts: str) -> str:
    """Rule 7: matched count against the registry size (coverage)."""
    block = r.get("kol") if isinstance(r.get("kol"), dict) else {}
    matched = block.get("kol") if isinstance(block.get("kol"), list) else []
    size = block.get("registry_size") or r.get("kol_registry_size")
    try:
        size_i = int(size)
    except (TypeError, ValueError):
        size_i = 0
    if not matched and not size_i:
        return f"KOL       {DASH} \u00b7 engine: registry not queried"
    if size_i:
        pct = 100.0 * len(matched) / size_i
        return f"KOL       {len(matched)}/{size_i} matched ({pct:.1f}%) \u00b7 engine \u00b7 {ts}"
    return f"KOL       {len(matched)} matched \u00b7 registry size unknown \u2192 coverage {DASH}"


def _wash(r: dict[str, Any], ts: str) -> str:
    """Rule 6: label wash recycling instead of reporting demand."""
    cohorts = r.get("cohorts") if isinstance(r.get("cohorts"), dict) else {}
    recycled = [
        (label, float(block["initial_buy_pct"]))
        for key, label in (("team", "Team"), ("insiders", "Insiders"), ("snipers", "Snipers"))
        if isinstance((block := cohorts.get(key)), dict)
        and isinstance(block.get("initial_buy_pct"), (int, float))
        and float(block["initial_buy_pct"]) > 100
    ]
    wash = r.get("wash_trading") if isinstance(r.get("wash_trading"), dict) else {}
    vol_verdict = str(wash.get("fake_volume_verdict") or "").lower()
    if recycled:
        detail = ", ".join(f"{label} {pct:.0f}%" for label, pct in recycled)
        return f"Wash      bought {detail} of supply \u2192 recycled, not demand \u00b7 engine \u00b7 {ts}"
    if vol_verdict == "likely":
        return f"Wash      fake volume likely \u00b7 engine \u00b7 {ts}"
    if vol_verdict == "organic":
        return f"Wash      organic volume \u00b7 engine \u00b7 {ts}"
    return ""


def _market(r: dict[str, Any], ts: str) -> str:
    """Rule 8: compact market block, always last (before the footer)."""
    liq = r.get("liquidity_usd") if r.get("liquidity_usd") is not None else r.get("liquidity")
    bits = [
        f"price {_fmt_market_value(r.get('price'))}",
        f"mcap {_fmt_market_value(r.get('mcap'))}",
        f"liq {_fmt_market_value(liq)}",
        f"vol24 {_fmt_market_value(r.get('volume_24h'))}",
    ]
    joined = " \u00b7 ".join(bits)
    return f"Market    {joined} \u00b7 {source_for(r, 'liquidity')} \u00b7 {ts}"


def collect(r: dict[str, Any], ts: str = "") -> Signals:
    """Build every card signal once, so the renderer only lays them out."""
    safety, hard = safety_row(r, ts)
    top10 = r.get("top10_pct")
    if isinstance(r.get("holder_concentration"), dict):
        top10 = r["holder_concentration"].get("top10_pct", top10)
    try:
        top10_str = f"{float(top10):.1f}%"
    except (TypeError, ValueError):
        top10_str = None
    if top10_str in ("0.0%", DASH):  # rule 2: never print an unmeasured zero
        top10_str = None
    has_evidence = bool(
        isinstance(r.get("cohorts"), dict)
        or r.get("honeypot") is not None
        or _mint_auth(r) is not None
        or r.get("deployer_profile")
    )
    return Signals(
        safety=safety,
        hard_fail=hard,
        clusters=cluster_lines(r, ts),
        dev=_dev(r, ts),
        kol=_kol(r, ts),
        wash=_wash(r, ts),
        market=_market(r, ts),
        top10=top10_str,
        has_evidence=has_evidence,
    )
