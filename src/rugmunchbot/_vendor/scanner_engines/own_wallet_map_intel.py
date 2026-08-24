# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/own_wallet_map_intel.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""intel"""

from __future__ import annotations
"""Holder/funder classification + CEX-funding intel (de-godding 2026-08-23)."""


from rugmunchbot._vendor.logging_shim import get_logger
from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot._vendor.scanner_engines.own_wallet_map_support import (
    _CEX_DISPLAY,
    _FISH_PCT,
    _FRESH_WALLET_DAYS,
    _KNOWN_ROUTER_WALLETS,
    ROLE_EMOJI,
    _WHALE_PCT,
    _is_fresh,
    _to_iso,
)

def _cex_display_name(key: str | None) -> str:
    """Human display name for a CEX registry key (binance -> Binance)."""
    if not key:
        return "CEX"
    norm = str(key).lower().replace("_", " ")
    if norm in _CEX_DISPLAY:
        return _CEX_DISPLAY[norm]
    return " ".join(w.capitalize() for w in norm.split())

def _classify_holders(holders: list[dict[str, Any]], deployer: str | None = None) -> list[dict[str, Any]]:
    """Assign exactly one role per holder (first match wins by precedence).

    Precedence: Dev > Bundle > Fresh > Whale > Fish > Shrimp. "Fresh" only
    applies to sub-whale holders — a brand-new 6% wallet is still a whale.
    """
    deployer_l = (deployer or "").lower().strip()
    bundle_funders = _bundle_funders(holders)
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(days=_FRESH_WALLET_DAYS)

    classified: list[dict[str, Any]] = []
    for h in holders:
        addr = h["address"]
        pct = float(h.get("percentage", 0) or 0)
        first_ts = h.get("first_tx_ts")
        funder = h.get("funder")

        if deployer_l and addr == deployer_l:
            role = "dev"
        elif funder and funder in bundle_funders:
            role = "bundle"
        elif first_ts is not None and _is_fresh(first_ts, cutoff) and pct < _WHALE_PCT:
            role = "fresh"
        elif pct >= _WHALE_PCT:
            role = "whale"
        elif pct >= _FISH_PCT:
            role = "fish"
        else:
            role = "shrimp"

        classified.append(
            {
                "address": addr,
                "role": role,
                "percentage": pct,
                "first_tx_ts": _to_iso(first_ts),
                "funder": funder,
                "emoji": ROLE_EMOJI[role],
            }
        )
    return classified

def _bundle_funders(holders: list[dict[str, Any]]) -> set[str]:
    """Funding sources shared by >= 2 of the classified holders.

    Known DEX routers/aggregators are excluded: when several holders bought
    through the same router, it shows up as everyone's first funder without
    implying a coordinated bundle.
    """
    counts: dict[str, int] = {}
    for h in holders:
        funder = h.get("funder")
        if funder and funder not in _KNOWN_ROUTER_WALLETS:
            counts[funder] = counts.get(funder, 0) + 1
    return {funder for funder, n in counts.items() if n >= 2}

def _classify_funder_cex(funder_addr: str | None, chain_norm: str) -> str | None:
    """Exchange display name when the funder is a known CEX hot wallet.

    Checks the internal ``entity_registry`` (~28 CEX hot-wallet labels) first,
    then falls back to the per-chain ``known_entities`` label table for wider
    coverage. Returns ``None`` when the funder is not a recognized CEX — the
    honest OTC / fresh / unknown bucket.
    """
    if not funder_addr:
        return None
    try:
        from app.entity_registry import (
            ENTITY_TYPE_CEX_HOT_WALLET,
            ENTITY_TYPE_EXCHANGE,
            get_entity_registry,
        )

        match = get_entity_registry().classify_address(funder_addr)
        if match and match.entity_type in (ENTITY_TYPE_CEX_HOT_WALLET, ENTITY_TYPE_EXCHANGE):
            return _cex_display_name(match.entity_name)
    except Exception as exc:
        logger.debug("wallet_map_cex_registry_failed: %s", exc)
    try:
        from rugmunchbot._vendor.scanner_engines.known_entities import get_exchange_name

        name = get_exchange_name(funder_addr, chain_norm)
        if name and str(name).lower() != "unknown":
            return _cex_display_name(str(name))
    except Exception as exc:
        logger.debug("wallet_map_cex_known_entities_failed: %s", exc)
    return None

def _classify_funder_mixer(funder_addr: str | None, chain_norm: str) -> str | None:
    """Mixer name when the funder is a known mixer (Tornado Cash, ...)."""
    if not funder_addr:
        return None
    try:
        from app.entity_registry import ENTITY_TYPE_MIXER, get_entity_registry

        match = get_entity_registry().classify_address(funder_addr)
        if match and match.entity_type == ENTITY_TYPE_MIXER:
            return str(match.entity_name)
    except Exception as exc:
        logger.debug("wallet_map_mixer_registry_failed: %s", exc)
    try:
        from rugmunchbot._vendor.scanner_engines.known_entities import label_wallet

        label = label_wallet(funder_addr, chain_norm)
        if label.get("type") == "mixer":
            return str(label.get("name") or "mixer")
    except Exception as exc:
        logger.debug("wallet_map_mixer_known_entities_failed: %s", exc)
    return None

def _compute_cex_funding(
    classified: list[dict[str, Any]],
    chain_norm: str,
) -> tuple[dict[str, int], int, int]:
    """Per-CEX first-funder counts over the classified holders.

    Each holder with a known first-funder is counted once under its CEX
    (a Binance hot wallet funding 5 holders counts 5 times — the "% of
    wallets funded by Binance" semantics). Funders that are not a recognized
    CEX land in the ``unknown`` bucket (OTC / fresh / unlabeled).

    Returns ``(breakdown, unknown_count, total)`` where ``total`` is the
    number of classified holders with a non-null first-funder.
    """
    breakdown: dict[str, int] = {}
    unknown = 0
    total = 0
    for entry in classified:
        funder = entry.get("funder")
        if not funder:
            continue
        total += 1
        cex = _classify_funder_cex(funder, chain_norm)
        if cex:
            breakdown[cex] = breakdown.get(cex, 0) + 1
        else:
            unknown += 1
    return breakdown, unknown, total

def _build_cex_funding_line(breakdown: dict[str, int], unknown: int, total: int) -> str:
    """Compact funding line, e.g. ``🏦 CEX funding: Binance 38% · Coinbase 12% · unknown 50%``.

    Top CEXes (max 4) + the unknown bucket. Fail-open to "—" when no holder
    has a resolvable first-funder.
    """
    if total <= 0:
        return "\u2014"
    parts: list[str] = []
    for cex, count in sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)[:4]:
        parts.append(f"{cex} {round(count / total * 100)}%")
    if unknown:
        parts.append(f"unknown {round(unknown / total * 100)}%")
    if not parts:
        return "\u2014"
    return "\U0001f3e6 CEX funding: " + " \u00b7 ".join(parts)
