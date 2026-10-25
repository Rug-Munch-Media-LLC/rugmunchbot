"""Diffable snapshot of the facts a watch alerts on.

A watch never re-derives risk: it compares what the token looked like on the
previous cycle with what it looks like now, and only a *change* is an event.
That makes every alert answerable — what changed, from what, to what — and a
re-scan of an unchanged token emits nothing at all.

The source is the unified scanner payload. Every field is read defensively
because premium modules drop out when a provider degrades: a missing value is
``None``, never zero, so "unknown" can never masquerade as "safe".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _num(value: Any) -> float | None:
    """Numeric value or ``None`` — never coerce unknown into zero."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _authority(payload: dict[str, Any]) -> dict[str, str | None]:
    raw = payload.get("contract_authority") or {}
    return {
        "mint": raw.get("mint_authority") or payload.get("mint_authority"),
        "freeze": raw.get("freeze_authority") or payload.get("freeze_authority"),
        "owner": raw.get("owner") or payload.get("owner"),
    }


def _lp(payload: dict[str, Any]) -> dict[str, Any]:
    liquidity = payload.get("liquidity") or {}
    authority = payload.get("contract_authority") or {}
    usd = _num(liquidity.get("usd"))
    if usd is None:
        usd = _num(payload.get("liquidity_usd"))
    if usd == 0.0 and not liquidity:
        # A zero from a scan that reported no pool block is *absent data*, not an
        # empty pool. Keeping it would let ``lp_unlocked`` fire a false "drained".
        usd = None
    locked = _num(liquidity.get("locked_pct"))
    if locked is None:
        locked = _num(authority.get("lp_locked_pct"))
    return {
        "liquidity_usd": usd,
        "locked_pct": locked,
        "unlocked": None if locked is None else locked == 0.0,
        "owner": authority.get("owner"),
    }


def _holders(payload: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for holder in payload.get("top_holders") or []:
        if not isinstance(holder, dict):
            continue
        wallet = str(holder.get("address") or holder.get("wallet") or "")
        pct = _num(holder.get("pct"))
        if pct is None:
            pct = _num(holder.get("percentage"))
        if wallet:
            rows.append({"wallet": wallet, "pct": pct})
    return {
        "count": payload.get("holder_count"),
        "top": rows[:50],
        "dev_pct": _num(payload.get("dev_holdings_pct")),
    }


def _bundle_wallets(payload: dict[str, Any]) -> list[str]:
    found: list[str] = []
    bundles = (payload.get("bundle_analysis") or {}).get("bundles") or []
    for bundle in bundles:
        if isinstance(bundle, dict):
            found.extend(str(w) for w in (bundle.get("wallets") or []))
    first_buyers = payload.get("bundled_first_buyers")
    if isinstance(first_buyers, (list, tuple, set)):
        found.extend(str(w) for w in first_buyers)
    # An int here is a *count* of bundled first buyers, not a wallet list; the
    # EVM scanner emits both shapes and iterating the int used to crash. Keep
    # the count as a count — ``bundle_wallets`` is a set of addresses or empty.
    return sorted({wallet for wallet in found if wallet})


def snapshot_from_scan(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the comparable snapshot for one scanner payload.

    Args:
        payload: unified scanner result (``/api/v2/scanner/token/scan``).

    Returns:
        JSON-ready snapshot with ``token``, ``chain``, ``captured_at``,
        ``authority``, ``lp``, ``holders`` and ``bundle_wallets``.

    Raises:
        ValueError: if the payload carries no token, because a snapshot
            without an identity cannot be stored or diffed.
    """
    token = str(payload.get("token") or "").strip()
    if not token:
        raise ValueError("payload has no token address")
    return {
        "token": token,
        "chain": str(payload.get("chain") or ""),
        "captured_at": str(payload.get("scanned_at") or datetime.now(UTC).isoformat()),
        "authority": _authority(payload),
        "lp": _lp(payload),
        "holders": _holders(payload),
        "bundle_wallets": _bundle_wallets(payload),
    }
