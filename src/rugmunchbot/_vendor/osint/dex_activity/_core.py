# VENDORED 2026-08-17 from rmi-backend/app/osint/dex_activity/_core.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
OSINT suspicious DEX activity — wash-trading detector for DEX pairs.

Given a DEX pair address (or a token address in ``chain="auto"`` mode), pull
public pair data from DexScreener (no API key required) and score it for
wash-trading / fake-volume behaviour:

- **volume/liquidity divergence** — high 24h volume with little or no
  liquidity (volume:liquidity turnover far above organic market norms).
- **buy/sell symmetry** — near-equal buy and sell counts over 24h, the
  round-trip signature of self-dealing.
- **liquidity churn / volatility** — zero liquidity with nonzero volume, or
  extreme turnover on a very young pair. *Proxy:* the free DexScreener API
  does not expose historical LP add/remove events, so churn is inferred from
  liquidity state, turnover and pair age.
- **trader concentration** — few trades moving a lot of volume, i.e. a small
  set of actors dominating the pair. *Proxy:* unique-trader counts are not
  exposed by the free API, so concentration is inferred from average trade
  size and txn count.

Each signal becomes an :class:`~app.osint.OsintFinding`
(``source="dexscreener"``, ``category="wash_trading"``) with its own
confidence, and the module emits an aggregate ``wash_trade_risk``
(``low``/``medium``/``high``) computed from the per-signal scores.

Contract: ``lookup_pair_activity(pair_address, *, chain="auto", timeout=60)
-> OsintReport`` (see :mod:`app.osint`). API failures and empty results are
recorded in ``report.errors`` — this never raises.

Reuses the same public DexScreener endpoint patterns already used across the
codebase (``app.unified_provider``, ``app.token_watch``,
``app.routers.token_page`` …) rather than adding a client. Those are async
provider methods; OSINT modules are sync/blocking by contract (the engine
runs them in a ``ThreadPoolExecutor``), so this module calls the API directly
with sync ``httpx``, exactly like :mod:`app.osint.wallet` does.

Dependencies: stdlib + ``httpx`` (already pinned in pyproject).

Usage::

    from rugmunchbot._vendor.osint.dex_activity import lookup_pair_activity

    report = lookup_pair_activity("0xe6ff8b9A37B0fab776134636D9981Aa778c4e718", chain="ethereum", timeout=30)
    print(report.to_dict())
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final, cast

try:
    import httpx
except ImportError as exc:  # pragma: no cover — httpx is a pinned runtime dependency
    from rugmunchbot._vendor.osint import OsintNotAvailable

    raise OsintNotAvailable("dex activity detection requires httpx: pip install httpx") from exc

from rugmunchbot._vendor.osint import OsintReport
from rugmunchbot._vendor.osint.dex_activity._common import (
    _add_finding,
    _num,
    _pair_label,
    _subdict,
)
from rugmunchbot._vendor.osint.dex_activity._signals import (
    _analyze_pair_signals,
    _context_finding,
    _risk_level,
    _signal_score,
)

logger = logging.getLogger(__name__)

__all__ = ["lookup_pair_activity"]

# ─── Tunables ────────────────────────────────────────────────────────

#: DexScreener public API base (no key; see app.routers.token_page for the same base).
DEXSCREENER_API: Final = "https://api.dexscreener.com/latest/dex"

#: Chain aliases normalised to DexScreener's chainId values (pass-through otherwise).
CHAIN_ALIASES: Final[dict[str, str]] = {
    "eth": "ethereum",
    "sol": "solana",
    "bnb": "bsc",
    "matic": "polygon",
    "arb": "arbitrum",
    "op": "optimism",
}

EVM_PAIR_RE: Final = re.compile(r"^0x[0-9a-fA-F]{40}$")
BASE58_PAIR_RE: Final = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

#: Max pairs analyzed per lookup in auto mode (ranked by liquidity desc).
MAX_PAIRS: Final = 8


# ─── Data fetch ──────────────────────────────────────────────────────


def _parse_pairs(payload: Any) -> list[dict[str, Any]]:
    """Extract the ``pairs`` list from a DexScreener payload, tolerating garbage."""
    if not isinstance(payload, dict):
        return []
    body = cast("dict[str, Any]", payload)
    raw = body.get("pairs")
    if not isinstance(raw, list):
        return []
    # mypy sees list[Unknown] as already list[Any]-compatible (redundant-cast);
    # pyright keeps the element type Unknown unless we force list[Any] here.
    return [cast("dict[str, Any]", p) for p in cast("list[Any]", raw) if isinstance(p, dict)]  # type: ignore[redundant-cast]


def _fetch_pairs(address: str, chain: str, timeout: int) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch candidate pairs from DexScreener; returns ``(pairs, error)``.

    With an explicit ``chain``, try the exact pair endpoint first
    (``/pairs/{chain}/{address}``), then fall back to the token endpoint
    filtered by chain — so both a pair address and a token address work.
    With ``chain="auto"``, use the token endpoint and analyze every pair.
    Never raises: transport failures surface as the error string.
    """
    try:
        if chain and chain != "auto":
            resp = httpx.get(f"{DEXSCREENER_API}/pairs/{chain}/{address}", timeout=timeout)
            if resp.status_code == 200:
                pairs = _parse_pairs(resp.json())
                if pairs:
                    return pairs, None
            resp = httpx.get(f"{DEXSCREENER_API}/tokens/{address}", timeout=timeout)
            if resp.status_code == 200:
                pairs = [p for p in _parse_pairs(resp.json()) if str(p.get("chainId", "")).lower() == chain]
                if pairs:
                    return pairs, None
            logger.debug("dexscreener: no pairs for %s on %s", address, chain)
            return [], f"no pairs for chain={chain!r} (HTTP {resp.status_code})"

        resp = httpx.get(f"{DEXSCREENER_API}/tokens/{address}", timeout=timeout)
        if resp.status_code == 200:
            pairs = _parse_pairs(resp.json())
            if pairs:
                return pairs, None
            logger.debug("dexscreener: empty payload for %s", address)
            return [], "no pairs found (HTTP 200, empty payload)"
        logger.debug("dexscreener: HTTP %s for %s", resp.status_code, address)
        return [], f"HTTP {resp.status_code}"
    except (httpx.HTTPError, OSError) as exc:
        logger.debug("dexscreener fetch failed for %s on %s: %s", address, chain, exc)
        return [], str(exc)


# ─── Public API ──────────────────────────────────────────────────────


def lookup_pair_activity(pair_address: str, *, chain: str = "auto", timeout: int = 60) -> OsintReport:
    """Detect wash-trading / suspicious DEX activity for a pair or token address.

    Args:
        pair_address: DEX pair address (EVM ``0x…`` or Solana base58) — or a
            token address when ``chain="auto"``, in which case every pair
            listed for the token is analyzed.
        chain: ``"auto"`` (default) resolves via the token endpoint; otherwise
            a DexScreener chainId (``"ethereum"``, ``"bsc"``, ``"solana"``,
            ``"base"``, ``"arbitrum"`` …; common aliases like ``"eth"``/
            ``"sol"`` are normalised).
        timeout: per-HTTP-request timeout in seconds.

    Returns:
        Aggregated :class:`OsintReport`; each signal is an ``OsintFinding``
        with ``source="dexscreener"`` and ``category="wash_trading"``, and the
        final finding carries ``wash_trade_risk`` (low/medium/high). API
        failures and empty results land in ``report.errors`` — never raises.
    """
    report = OsintReport(target=pair_address, target_type="pair")

    address = pair_address.strip()
    if not address:
        report.errors.append("normalization: empty pair address")
        return report
    if not (EVM_PAIR_RE.match(address) or BASE58_PAIR_RE.match(address)):
        report.errors.append(f"normalization: unrecognized address format: {address[:24]!r}")
        return report

    chain_key = CHAIN_ALIASES.get(chain.strip().lower(), chain.strip().lower())
    pairs, fetch_err = _fetch_pairs(address, chain_key, timeout)
    if fetch_err is not None:
        report.errors.append(f"dexscreener: {fetch_err}")
        return report
    if not pairs:
        report.errors.append(f"dexscreener: no pair data for {address}")
        return report

    ranked = sorted(
        pairs,
        key=lambda p: _num(_subdict(p, "liquidity").get("usd")),
        reverse=True,
    )[:MAX_PAIRS]

    per_pair_risk: dict[str, str] = {}
    worst_score = 0.0
    worst_level = "low"
    for pair in ranked:
        report.findings.append(_context_finding(pair))
        signals = _analyze_pair_signals(pair)
        report.findings.extend(signals)
        score = _signal_score(signals)
        level = _risk_level(score)
        per_pair_risk[_pair_label(pair)] = level
        if score > worst_score:
            worst_score = score
            worst_level = level

    logger.debug(
        "dexscreener wash-trade analysis for %s: risk=%s score=%.1f pairs=%d",
        address,
        worst_level,
        worst_score,
        len(ranked),
    )
    _add_finding(
        report,
        source="dexscreener",
        category="wash_trading",
        summary=(f"aggregate wash-trade risk: {worst_level} (score {worst_score:.1f}, {len(ranked)} pair(s) analyzed)"),
        confidence="high" if worst_level == "high" else "medium",
        wash_trade_risk=worst_level,
        score=round(worst_score, 1),
        pairs_analyzed=len(ranked),
        per_pair_risk=per_pair_risk,
    )
    return report
