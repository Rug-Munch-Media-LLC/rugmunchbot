"""Scanner engine — token/wallet scan engines + databus intel fetchers.

``scan_token`` (legacy multi-source fallback), ``scan_token_v2`` (canonical
unified v2 POST), SENTINEL deep scan, hype/deployer databus fetches and the
wallet analyzers. ``backend_json_post`` / ``schedule_scan_memory_index``
resolve through the package so tests can patch ``tools.scanner.*``
(monkeypatch contract, see ``tools/scanner/__init__.py``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    SENTINEL_TIMEOUT,
    SENTINEL_URL,
    SOLANAFM_API,
    backend_headers,
)
from rugmunchbot.formatters import detect_chain, is_sol
from rugmunchbot.states import get_sol_price
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools import scanner as _scanner
from rugmunchbot.tools._client import http_get_json
from rugmunchbot.tools.scanner._deployer import _extract_pair_intel
from rugmunchbot.tools.scanner._market import _ds_market_snapshot
from rugmunchbot.tools.scanner._providers import (
    _safe_float,
    _scan_evm_security_provider,
    _scan_rmi_provider,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Consolidated risk-signal enrichment (bundle / deployer / wash / whale /
# authority / social cross-ref). Imported defensively so the bot still works
# if the module is not deployable on a given host.
enrich_scan: Callable[[dict[str, Any], str, str], Awaitable[dict[str, Any]]] | None
try:  # pragma: no cover - exercised only when scan_enrich is absent
    from rugmunchbot._vendor.scan_enrich import (
        enrich_scan as enrich_scan,
    )
except ImportError:
    enrich_scan = None

logger = get_logger("rmi_bot.tools.scanner")


async def scan_token(
    address: str,
    chain: str | None = None,
    *,
    security_only: bool = False,
) -> dict[str, Any]:
    """Legacy multi-source token scan (DexScreener + GoPlus + Honeypot.is +
    RMI databus). Used as the /scan fallback when the unified v2 scan
    (``scan_token_v2``) is unavailable, and by wallet/investigate/payments.

    ``security_only=True`` is the CHEAP monitor-delta path (the
    ``check_monitors`` background job): it skips market data (DexScreener),
    the RMI databus, GMGN/risk-signal enrichment and RAG indexing, and only
    re-fetches the EVM security chain (GoPlus → honeypot.is fallback) that
    populates the fields a monitor baseline watches — buy/sell tax,
    ownership-renounced, mint authority, LP lock and honeypot. One light
    upstream call per token instead of a full scan; the same fail-open
    guarantees (a slow/blocked provider degrades, never hangs).
    """
    result: dict[str, Any] = {
        "address": address,
        "chain": chain or detect_chain(address),
        "name": "Unknown",
        "symbol": "???",
        "price": None,
        "mcap": None,
        "fdv": None,
        "volume_24h": None,
        "liquidity": None,
        "price_change_1h": None,
        "price_change_6h": None,
        "price_change_24h": None,
        "price_change_7d": None,
        "volume_1h": None,
        "volume_6h": None,
        "holders": None,
        "pair_address": "",
        "dex": "",
        "created_at": None,
        "risk_score": 0,
        "threats": [],
        "contract_verified": None,
        "honeypot": None,
        "buy_tax": None,
        "sell_tax": None,
        "lp_locked": None,
        "mintable": None,
        "can_blacklist": None,
        "owner_renounced": None,
        "top_holders": [],
        "bundle_detected": False,
        "fresh_wallet_pct": 0,
        "exchange_pct": 0,
        "volume_real_ratio": None,
        "dev_wallet": None,
        "dev_other_tokens": [],
        "errors": [],
        "socials": {},
        "news_sentiment": None,
    }

    client = _scanner.get_shared_client(8.0)
    if security_only:
        # Security-only refresh: skip market data + RMI databus + enrichment,
        # just re-check the on-chain security properties a monitor watches.
        # Non-EVM tokens get the bare result (all security fields None) —
        # identical to what the full legacy scan already produces for them.
        await _scan_evm_security_provider(client, address, result)
        result["risk_score"] = min(result["risk_score"], 100)
        return result

    # DexScreener resolves market/identity AND the real pair chain first —
    # the EVM security provider needs the resolved chain to pick the GoPlus
    # chain id, so it stays the sequential lead-in (8s cap, fail-open).
    try:
        r = await client.get(f"{DEXSCREENER}/tokens/{address}", timeout=8.0)
        if r.status_code == 200:
            data = r.json()
            pairs = data.get("pairs", [])
            if pairs:
                # Best-pair snapshot: DexScreener returns many pairs per token
                # and ``pairs[0]`` is often a pump.fun bonding-curve pair with
                # zero liquidity — pick the pair with the most liquidity and
                # surface every real market field (see _ds_market_snapshot).
                snap = _ds_market_snapshot(pairs)
                # Identity + price MUST come from the BEST pair, not pairs[0]
                # — index 0 is often a $0-liquidity bonding-curve pair whose
                # price is garbage (the exact bug _ds_market_snapshot fixed
                # for market fields; price/name/symbol had the same disease).
                best_idx = 0
                best_score = (-1.0, 0.0)
                for i, pair in enumerate(pairs):
                    if not isinstance(pair, dict):
                        continue
                    liq = _safe_float((pair.get("liquidity") or {}).get("usd"))
                    vol = _safe_float((pair.get("volume") or {}).get("h24"))
                    if (liq, vol) > best_score:
                        best_score = (liq, vol)
                        best_idx = i
                p = pairs[best_idx]
                result["name"] = p.get("baseToken", {}).get("name", "Unknown")
                result["symbol"] = snap.get("symbol") or p.get("baseToken", {}).get("symbol", "???")
                # Null-safe numeric coercion: a null priceUsd (delisted /
                # no-pair payloads) must never crash the scan reply.
                result["price"] = _safe_float(p.get("priceUsd")) or None
                result["mcap"] = _safe_float(p.get("marketCap") or p.get("fdv")) or None
                result["fdv"] = _safe_float(p.get("fdv") or result["mcap"]) or None
                result["liquidity"] = snap.get("liquidity")
                result["volume_24h"] = snap.get("volume_24h")
                # Multi-timeframe price change (DexScreener priceChange has
                # m1/m5/h1/h6/h24 — no 7d window; absent fields stay None so
                # the formatter renders "—", never a fabricated 0).
                pc_tf = snap.get("price_change_tf") or {}
                result["price_change_1h"] = pc_tf.get("h1")
                result["price_change_6h"] = pc_tf.get("h6")
                result["price_change_24h"] = pc_tf.get("h24")
                result["price_change_7d"] = None
                result["volume_1h"] = (snap.get("volume_tf") or {}).get("h1")
                result["volume_6h"] = (snap.get("volume_tf") or {}).get("h6")
                result["pair_address"] = snap.get("pair_address") or p.get("pairAddress", "")
                result["dex"] = snap.get("dex") or p.get("dexId", "")
                result["chain"] = p.get("chainId", result["chain"])
                result["created_at"] = snap.get("created_at") or p.get("pairCreatedAt")
                # Deployer creator + socials (twitter/telegram/websites) from
                # the pair payload — real data only; absent stays absent.
                creator, pair_socials = _extract_pair_intel(p)
                if creator:
                    result["deployer_address"] = creator
                if pair_socials:
                    result["socials"].update(pair_socials)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        result["errors"].append(f"DexScreener: {e}")

    # Jupiter price fallback (keyless): when DexScreener has no pair/price
    # for a Solana mint (brand-new pump.fun launches), Jupiter's aggregated
    # price fills the hole. Fail-open; 6s cap.
    if result.get("price") is None and result.get("chain") == "solana":
        try:
            from rugmunchbot.tools.scanner._market import jupiter_price

            jup = await jupiter_price(client, address)
            if jup:
                result["price"] = jup["price_usd"]
                if jup.get("change_24h") is not None:
                    result["price_change_24h"] = jup["change_24h"]
                if jup.get("liquidity"):
                    result.setdefault("jup_liquidity", jup["liquidity"])
                result["threats"].append("Price via Jupiter (no DexScreener pair yet)")
        except Exception as exc:
            result["errors"].append(f"Jupiter: {exc}")

    # Independent providers run CONCURRENTLY (8s per-provider cap, fail-open):
    # the EVM security chain (GoPlus → honeypot.is fallback, kept sequential
    # inside the coroutine so the fallback gate stays exact) + the RMI
    # databus bundle/wallet check. A slow/blocked provider degrades to its
    # own error line — it never hangs the scan.
    await asyncio.gather(
        _scan_evm_security_provider(client, address, result),
        _scan_rmi_provider(client, address, result),
        return_exceptions=True,
    )

    # Volume authenticity
    if (result["volume_24h"] or 0) > 0 and (result["mcap"] or 0) > 0:
        ratio = result["volume_24h"] / result["mcap"]
        result["volume_real_ratio"] = ratio
        if ratio > 5:
            result["threats"].append(f"Suspicious volume/mcap ratio: {ratio:.1f}x")
            result["risk_score"] += 15

    # Liquidity check
    if (result["mcap"] or 0) > 0 and (result["liquidity"] or 0) > 0:
        liq_ratio = result["liquidity"] / result["mcap"]
        if liq_ratio < 0.02:
            result["threats"].append("Very low liquidity vs mcap")
            result["risk_score"] += 20

    result["risk_score"] = min(result["risk_score"], 100)
    # GMGN holder/smart-money enrichment — best-effort + non-blocking
    # (hard 6s internal budget, fail-open; never raises).
    result = await _scanner.enrich_scan_with_gmgn(result, address, result["chain"])
    # Risk-signal enrichment (bundle / deployer reputation / wash trading /
    # whale flow / authority / social cross-ref) — best-effort + non-blocking
    # (hard 8s budget, fail-open per module; never raises). Attaches
    # ``risk_signals`` so the formatter can render the "why it's sus" block.
    if _scanner.enrich_scan is not None:
        result = await _scanner.enrich_scan(result, address, result["chain"])
    # Solana on-chain ground truth (mint/freeze authority, Token-2022 tax,
    # top-holder concentration, deployer fallback, same-slot bundle) —
    # fills whatever the providers dropped, fail-open, 9s hard budget.
    result = await _scanner.enrich_scan_solana(result, address, result["chain"])
    # Vybe labeled holders (Solana): entity tags (CEX/KOL/treasury) on top
    # holders + CEX-concentration threat when exchanges dominate supply.
    # Fills gaps left by on-chain enrichment; fail-open, best-effort.
    if result.get("chain") == "solana":
        try:
            from rugmunchbot.tools.vybe import vybe_top_holders

            vholders = await vybe_top_holders(address, limit=10)
            if vholders:
                existing = {h.get("address"): h for h in result.get("top_holders", [])}
                cex_pct = 0.0
                for vh in vholders:
                    slot = existing.get(vh["address"])
                    if slot is not None and not slot.get("tag"):
                        slot["tag"] = vh["owner_name"]
                    elif slot is None and len(result["top_holders"]) < 10:
                        result["top_holders"].append(
                            {
                                "address": vh["address"],
                                "pct": vh["pct"],
                                "is_contract": False,
                                "tag": vh["owner_name"],
                            }
                        )
                    name_l = (vh["owner_name"] or "").lower()
                    if any(w in name_l for w in ("binance", "coinbase", "okx", "bybit", "kraken", "upbit", "bithumb")):
                        cex_pct += vh["pct"]
                if cex_pct >= 30:
                    result["threats"].append(f"CEX wallets hold {cex_pct:.0f}% of supply")
                    result["risk_score"] += 10
        except Exception as exc:
            result["errors"].append(f"Vybe: {exc}")
    # Token-scoped smart money — replaces the wallet-scoped endpoint's empty
    # rows with real tracked-wallet flow (fail-open, 6s budget).
    result = await _scanner.enrich_scan_smart_money(result, address, result["chain"])
    # Scan-to-RAG memory: auto-index the summary into Qdrant so future
    # chat queries can recall "have we scanned this token before?" with
    # risk context. Background + fail-open — never blocks the response.
    if _scanner.should_index_scan(result, source="scan_v1"):
        _scanner.schedule_scan_memory_index(result, source="scan_v1")
    return result


async def scan_token_v2(address: str, chain: str | None = None) -> dict[str, Any] | None:
    """Canonical v2 UnifiedTokenScanner — explainable multi-dimensional scoring.

    POSTs to ``/api/v2/scanner/token/scan`` through the tools' shared
    ``backend_json_post`` client (the SAME client ``scanner_intel`` uses —
    unified 2026-08-15; the legacy scanner previously built its own
    ``httpx.AsyncClient`` for this exact endpoint). Returns the raw v2
    payload (safety_score, confidence, risk_level, risk_flags, green_flags,
    breakdown, evidence, modules_run). Returns None on any failure so
    callers can fall back to the legacy scanner.
    """
    chain_norm = chain or detect_chain(address)
    try:
        data = await _scanner.backend_json_post(
            "/api/v2/scanner/token/scan",
            json={"address": address, "chain": chain_norm},
            timeout=45.0,
        )
    except _scanner.BackendApiError as exc:
        # Fail-loud helper, but scan_token_v2 is the FIRST leg of a
        # fallback chain (v2 → legacy bot scan → DexScreener): degrade to a
        # miss with the status logged, let the callers fall back.
        logger.warning("scan_token_v2 backend error %s for %s: %s", exc.status_code, address, exc)
        return None
    if data is None:
        logger.warning("scan_token_v2 failed for %s", address)
        return None
    if data.get("status") != "ok":
        logger.warning("scan_token_v2 non-ok status for %s", address)
        return None
    # GMGN holder/smart-money enrichment — best-effort + non-blocking
    # (hard 6s internal budget, fail-open; never raises).
    data = await _scanner.enrich_scan_with_gmgn(data, address, chain_norm)
    # Risk-signal enrichment — best-effort + non-blocking, see scan_token.
    if _scanner.enrich_scan is not None:
        data = await _scanner.enrich_scan(data, address, chain_norm)
    # Solana on-chain ground truth + token-scoped smart money (fail-open,
    # see scan_token for the budget/fail-open contract).
    data = await _scanner.enrich_scan_solana(data, address, chain_norm)
    data = await _scanner.enrich_scan_smart_money(data, address, chain_norm)
    # Scan-to-RAG memory (background, fail-open) — see scan_token.
    if _scanner.should_index_scan(data, source="scan_v2"):
        _scanner.schedule_scan_memory_index(data, source="scan_v2")
    return data


async def sentinel_scan(address: str, chain: str | None = None) -> dict[str, Any] | None:
    """Deep forensic scan — POST /api/v2/scanner/token/deepscan.

    Runs ALL scanner modules including premium/SENTINEL (bundle detect,
    cluster map, dev finder, sniper, bot farm, copy trade, insider, wash
    trade, MEV, fresh wallets, holder analysis, LP verification, dev
    reputation, metadata fingerprint, contract authority, proxy detection).
    Free — no x402 payment required.

    Returns the v2 scanner payload enhanced with extra keys for backward
    compat with the old sentinel report formatters. Returns None on any
    failure so callers can fall back to the base /scan.
    """
    chain_norm = chain or detect_chain(address)
    try:
        client = _scanner.get_shared_client(SENTINEL_TIMEOUT)
        r = await client.post(
            SENTINEL_URL,
            json={"address": address, "chain": chain_norm},
            headers=backend_headers(),
            timeout=SENTINEL_TIMEOUT,
        )
        if r.status_code != 200:
            logger.warning("deepscan status %s for %s", r.status_code, address)
            return None
        data = r.json()
        if not isinstance(data, dict):
            logger.warning("deepscan non-dict response for %s", address)
            return None
        if data.get("status") != "ok":
            logger.warning("deepscan non-ok status for %s", address)
            return None
        data.setdefault("address", address)
        data.setdefault("chain", chain_norm)
        data["_source"] = "deepscan"
        # Provide backward-compat alias keys for the sentinel formatter
        evidence = data.get("evidence") or {}
        data.setdefault("composite_risk_score", 100 - (data.get("safety_score") or 0))
        data.setdefault("red_flags", data.get("risk_flags", []))
        data.setdefault("modules_run", data.get("modules_run", []))
        # Flatten evidence into top-level sentinel sections
        for section in (
            "holder_analysis",
            "liquidity_verification",
            "dev_reputation",
            "contract_authority",
            "bundle_analysis",
            "wallet_metrics",
        ):
            if section not in data and isinstance(evidence, dict):
                data[section] = evidence.get(section, {})
        # Solana on-chain ground truth + token-scoped smart money — deep
        # scans get the same always-available facts as regular scans.
        data = await _scanner.enrich_scan_solana(data, address, chain_norm)
        data = await _scanner.enrich_scan_smart_money(data, address, chain_norm)
        # Scan-to-RAG memory (background, fail-open)
        _scanner.schedule_scan_memory_index(data, source="deepscan")
        return data
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("deepscan failed for %s: %s", address, e)
        return None


async def fetch_hype_score(address: str, chain: str | None = None) -> dict[str, Any] | None:
    """Fetch the C5 hype score from the backend DataBus ``hype_score`` provider.

    GETs /api/v1/databus/fetch/hype_score and returns the inner ``data``
    payload::

        {
            "hype_score": 0-100,
            "signals": {
                "social_velocity": {"score", "detail", ...},
                "volume_growth": {"score", "detail", ...},
                "holder_growth": {"score", "detail", ...},
                "price_momentum": {"score", "detail", ...},
            },
            "decay_factor": ...,
            "inactivity_hours": ...,
        }

    Returns None on any failure so callers can fall back to the local
    DexScreener-based hype computation.
    """
    try:
        params: dict[str, str] = {"address": address}
        if chain:
            params["chain"] = chain
        payload = await http_get_json(
            f"{BACKEND_URL}/api/v1/databus/fetch/hype_score",
            params=params,
            headers=backend_headers(),
            timeout=20,
        )
        if payload is None:
            logger.warning("fetch_hype_score failed for %s", address)
            return None
        data = payload.get("data") or payload
        if not isinstance(data, dict) or data.get("result") != "scanned":
            logger.warning("fetch_hype_score non-scanned result for %s", address)
            return None
        return data
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("fetch_hype_score failed for %s: %s", address, e)
        return None


async def fetch_deployer_fingerprint(
    deployer: str, chain: str | None = None
) -> dict[str, Any] | None:
    """Fetch the C7 deployer behavioral fingerprint from the backend DataBus.

    GETs /api/v1/databus/fetch/deployer_fingerprint and returns the inner
    ``data`` payload::

        {
            "deployer": "0x...",
            "risk_bucket": "NEW" | "REPEAT_CLEAN" | "REPEAT_RUG" | "FARM" | "PROLIFIC",
            "risk_score": 0-100,
            "total_deployments": int,
            "success_rate": float,
            "median_lifespan_hours": float,
            "funder_profile": {...},
            "launchpad": "pump.fun" | ...,   # only when factory-resolved
            "real_deployer": "0x...",        # only when factory-resolved
        }

    Returns None on any failure so callers can fall back to the token-scan
    deployer evidence.
    """
    try:
        params: dict[str, str] = {"deployer": deployer}
        if chain:
            params["chain"] = chain
        payload = await http_get_json(
            f"{BACKEND_URL}/api/v1/databus/fetch/deployer_fingerprint",
            params=params,
            headers=backend_headers(),
            timeout=20,
        )
        if payload is None:
            logger.warning("fetch_deployer_fingerprint failed for %s", deployer)
            return None
        data = payload.get("data") or payload
        if not isinstance(data, dict) or data.get("result") != "scanned":
            logger.warning("fetch_deployer_fingerprint non-scanned result for %s", deployer)
            return None
        return data
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("fetch_deployer_fingerprint failed for %s: %s", deployer, e)
        return None


async def analyze_wallet_v2(address: str, chain: str | None = None) -> dict[str, Any] | None:
    """v2 UnifiedWalletScanner — entity clustering, scam exposure, smart money.

    POSTs to ``/api/v2/scanner/wallet/scan`` through the tools' shared
    ``backend_json_post`` client (same client family as ``scan_token_v2``).
    Returns the raw v2 payload (entity_name, entity_type, persona,
    scam_exposure_pct, smart_money_overlap, behavior_patterns, risk_forecast,
    risk_score, risk_flags). Returns None on failure so callers can fall back
    to the legacy wallet analyzer.
    """
    chain_norm = chain or detect_chain(address)
    try:
        data = await _scanner.backend_json_post(
            "/api/v2/scanner/wallet/scan",
            json={"address": address, "chain": chain_norm},
            timeout=45.0,
        )
    except _scanner.BackendApiError as exc:
        # Fallback-chain leg (v2 → legacy wallet analyzer): degrade to a miss.
        logger.warning(
            "analyze_wallet_v2 backend error %s for %s: %s", exc.status_code, address, exc
        )
        return None
    if data is None:
        logger.warning("analyze_wallet_v2 failed for %s", address)
        return None
    if data.get("status") != "ok":
        logger.warning("analyze_wallet_v2 non-ok status for %s", address)
        return None
    return data


async def analyze_wallet(address: str, chain: str | None = None) -> dict[str, Any]:
    """Legacy wallet analyzer — RMI databus wallet tokens + Solana.fm fallback.

    Used as the /wallet fallback when the unified v2 wallet scan
    (``analyze_wallet_v2``) is unavailable.
    """
    result: dict[str, Any] = {
        "address": address,
        "chain": chain or detect_chain(address),
        "balance_usd": 0,
        "tx_count": 0,
        "first_seen": None,
        "wallet_age_days": 0,
        "top_tokens": [],
        "is_fresh": False,
        "is_exchange": False,
        "is_contract": False,
        "risk_score": 0,
        "flags": [],
        "recent_txs": [],
        "funding_source": None,
        "connected_wallets": [],
        "pnl_estimate": None,
        "errors": [],
        # funding analysis metrics (populated by v2 scanner when available)
        "exchange_funded_pct": None,
        "fresh_wallet_pct": None,
        "holder_overlap_pct": None,
    }
    client = _scanner.get_shared_client(15)
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/databus/wallet/{address}/tokens",
            headers=backend_headers(),
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            for k in [
                "balance_usd",
                "tx_count",
                "first_seen",
                "is_exchange",
                "is_contract",
                "risk_score",
                "flags",
                "funding_source",
                "pnl_estimate",
            ]:
                if k in data:
                    result[k] = data[k]
            result["top_tokens"] = data.get("top_tokens", [])[:10]
            result["recent_txs"] = data.get("recent_txs", [])[:5]
            result["connected_wallets"] = data.get("connected_wallets", [])[:5]
            if result["first_seen"]:
                try:
                    first = datetime.fromisoformat(result["first_seen"].replace("Z", "+00:00"))
                    result["wallet_age_days"] = (datetime.now(UTC) - first).days
                    result["is_fresh"] = result["wallet_age_days"] < 7
                except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
                    logger.debug("analyze_wallet_error", exc_info=True)
                    logger.warning(f"Failed to parse first_seen for {address}: {e}")
            return result
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"analyze_wallet RMI backend failed for {address}: {e}")
    if is_sol(address):
        try:
            r = await client.get(f"{SOLANAFM_API}/accounts/{address}")
            if r.status_code == 200:
                data = r.json()
                sol_price = await get_sol_price()
                # Null-safe: a null Solana.fm balance must not crash /wallet.
                result["balance_usd"] = _safe_float(data.get("balance")) / 1e9 * sol_price
                result["tx_count"] = data.get("transaction_count", 0)
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            httpx.HTTPError,
            httpx.TimeoutException,
        ) as e:
            result["errors"].append(f"Solana.fm: {e}")
    return result
