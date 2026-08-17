"""Scanner engine — payload shape converters (v1/v2/DexScreener -> intel block).

Maps the unified v2 scan payload, the fast bot scan and the DexScreener
fallback into the chat-presentable intel block, plus the v2 -> legacy
report converters used by /scan and /wallet formatting.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.tools.scanner._deployer import _EMPTY_ADDR
from app.domains.telegram.rugmunchbot.tools.scanner._providers import _safe_float
from app.domains.telegram.rugmunchbot.tools.scanner._security import _honeypot_verdict, _security_checks


def _wash_trading_block(market: dict[str, Any]) -> dict[str, Any]:
    """Build the ``wash_trading`` intel block from the engine's market payload.

    Carries the full volume-authenticity evidence so the report formatter can
    render the dedicated 📉 Fake Volume verdict line: fake-volume %, the
    self-deal (buyer/seller-overlap) counts, the volume-to-holder churn ratio
    and the engine's own "likely"/"organic" verdict. Every field is optional —
    a provider that did not measure it stays None (never a fabricated 0).
    """
    return {
        "fake_volume_pct": market.get("fake_volume_pct"),
        "authentic_score": market.get("authentic_score"),
        "risk_level": market.get("risk_level"),
        "self_deal_wallets": market.get("self_deal_wallets"),
        "self_deal_swaps": market.get("self_deal_swaps"),
        "self_deal_swap_ratio": market.get("self_deal_swap_ratio"),
        "active_holders": market.get("active_holders"),
        "volume_to_holder_ratio": market.get("volume_to_holder_ratio"),
        "wash_signals": market.get("wash_signals"),
        "fake_volume_verdict": market.get("fake_volume_verdict"),
        "fake_volume_detail": market.get("fake_volume_detail"),
        "confidence": market.get("confidence"),
    }


def _flatten_v2(body: dict[str, Any], address: str, chain: str) -> dict[str, Any]:
    """Map the unified v2 scan payload into the chat-presentable intel block."""
    evidence = body.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    sentinel = body.get("sentinel")
    if not isinstance(sentinel, dict):
        sentinel = {}
    free = sentinel.get("free")
    if not isinstance(free, dict):
        free = {}
    holder_analysis = free.get("holder_analysis")
    if not isinstance(holder_analysis, dict):
        holder_analysis = {}
    liquidity_verification = free.get("liquidity_verification")
    if not isinstance(liquidity_verification, dict):
        liquidity_verification = {}
    funding_analysis = evidence.get("funding_analysis")
    if not isinstance(funding_analysis, dict):
        funding_analysis = {}
    holder_overlap = evidence.get("holder_overlap")
    if not isinstance(holder_overlap, dict):
        holder_overlap = {}
    market = evidence.get("market")
    if not isinstance(market, dict):
        market = {}
    bundle = body.get("bundle_analysis")
    if not isinstance(bundle, dict):
        bundle = {}
    wallet_metrics = body.get("wallet_metrics")
    if not isinstance(wallet_metrics, dict):
        wallet_metrics = {}

    # Deployer address: backend dev-reputation evidence first, then the
    # SENTINEL metadata-fingerprint ``creator`` (Pump.fun — free tier runs
    # this module even when the deployer reputation module does not).
    dev = evidence.get("deployer")
    if not isinstance(dev, dict):
        dev = {}
    deployer_addr = str(dev.get("deployer_address") or dev.get("address") or "").strip()
    if not deployer_addr or deployer_addr.lower() in _EMPTY_ADDR:
        mf = free.get("metadata_fingerprint") if isinstance(free, dict) else None
        if isinstance(mf, dict):
            deployer_addr = str(mf.get("creator") or "").strip()
    deployer_addr = deployer_addr if deployer_addr.lower() not in _EMPTY_ADDR else ""

    safety = _safe_float(body.get("safety_score"))
    risk_score = round(100.0 - safety, 2)

    return {
        "source": "backend:/api/v2/scanner/token/scan",
        "status": body.get("status"),
        "scanned_at": body.get("scanned_at"),
        "cached": bool(body.get("cached")),
        "tier": body.get("tier"),
        "background_status": body.get("background_status"),
        "fallback_used": bool(body.get("fallback_used")),
        "modules_run": body.get("modules_run"),
        "scan_duration_ms": body.get("scan_duration_ms"),
        # Risk
        "safety_score": safety,
        "risk_score": risk_score,
        "risk_level": body.get("risk_level") or "unknown",
        "confidence": body.get("confidence"),
        "risk_flags": body.get("risk_flags") or [],
        "green_flags": body.get("green_flags") or [],
        "breakdown": body.get("breakdown"),
        # Honeypot (from our security evidence checks)
        "honeypot": _honeypot_verdict(evidence),
        "security_checks": _security_checks(evidence),
        # Bundle detection (own ClickHouse engine)
        "bundle_analysis": {
            "result": bundle.get("result"),
            "is_bundled": bundle.get("is_bundled"),
            "risk_label": bundle.get("risk_label"),
            "confidence": bundle.get("confidence"),
            "launch_cluster_score": bundle.get("launch_cluster_score"),
            "common_funder_score": bundle.get("common_funder_score"),
            "similar_amount_score": bundle.get("similar_amount_score"),
            "deployer_funded_pct": bundle.get("deployer_funded_pct"),
            "first_time_buyer_pct": bundle.get("first_time_buyer_pct"),
            # Wallet count surfaced in the deep report's bundle verdict line.
            "launch_buyers": bundle.get("launch_buyers"),
            "cluster_blocks": bundle.get("cluster_blocks"),
            "common_funders": bundle.get("common_funders"),
        },
        # Wash-trading verdict (own volume-authenticity engine)
        "wash_trading": _wash_trading_block(market),
        # Holder analysis (own engines + SENTINEL)
        "holder_analysis": {
            "total_holders": holder_analysis.get("total_holders"),
            "hhi": holder_analysis.get("hhi"),
            "hhi_risk_level": holder_analysis.get("risk_level"),
            "top_10_pct": holder_analysis.get("top_10_percentage"),
            "top_20_pct": holder_analysis.get("top_20_percentage"),
            "top_50_pct": holder_analysis.get("top_50_percentage"),
            "fake_diversification_score": holder_analysis.get("fake_diversification_score"),
            "exchange_funded_pct": holder_analysis.get("exchange_funded_percentage"),
            "new_wallets_pct": holder_analysis.get("new_wallets_percentage"),
            "warnings": holder_analysis.get("warnings"),
        },
        "wallet_metrics": wallet_metrics,
        "funding_analysis": funding_analysis,
        "holder_overlap": holder_overlap,
        # Liquidity verification (SENTINEL module)
        "liquidity_verification": {
            "total_locked_pct": liquidity_verification.get("total_locked_percentage"),
            "lp_burned": liquidity_verification.get("lp_burned"),
            "fake_lock_detected": liquidity_verification.get("fake_lock_detected"),
            "risk_level": liquidity_verification.get("risk_level"),
        },
        # Deployer intel
        "deployer_profile": body.get("deployer_profile"),
        "deployer_address": deployer_addr or None,
        "dev_wallet": deployer_addr or None,
        # Socials (dict of type -> url; empty when the backend carries none —
        # the caller's DexScreener supplement fills them from pair ``info``).
        "socials": body.get("socials") if isinstance(body.get("socials"), dict) else {},
        # Market
        "price_usd": body.get("price_usd"),
        "market_cap_usd": body.get("market_cap_usd"),
        "liquidity_usd": body.get("liquidity_usd"),
        "volume_24h_usd": body.get("volume_24h_usd"),
        "age_hours": body.get("age_hours"),
        # Enrichment
        "news_sentiment": body.get("news_sentiment"),
        "news_card": body.get("news_card"),
        "rag_context": body.get("rag_context"),
        "data_sources": body.get("data_sources"),
        "sentinel_premium_preview": sentinel.get("premium_preview"),
    }


def _flatten_v1(body: dict[str, Any], address: str, chain: str) -> dict[str, Any]:
    """Map the fast bot scan payload into the same intel block shape."""
    return {
        "source": "backend:/api/v1/bot/scan",
        "status": "ok",
        "scanned_at": body.get("scanned_at"),
        "cached": bool(body.get("cached")),
        "tier": "free",
        "background_status": "complete",
        "fallback_used": False,
        "modules_run": ["fast_bot_scan"],
        "scan_duration_ms": None,
        # Risk (quick scan exposes a coarse 0-100 score)
        "safety_score": round(100.0 - _safe_float(body.get("risk_score")), 2),
        "risk_score": body.get("risk_score"),
        "risk_level": "unknown",
        "confidence": None,
        "risk_flags": [],
        "green_flags": [],
        "breakdown": None,
        "honeypot": "detected" if body.get("is_honeypot") else "none",
        "security_checks": {
            "HP01": {"status": "fail" if body.get("is_honeypot") else "pass", "details": ""},
            "CR03": {"status": "fail" if body.get("is_mintable") else "pass", "details": "Mintable"},
        },
        "bundle_analysis": None,
        "wash_trading": _wash_trading_block({}),
        "holder_analysis": {
            "total_holders": body.get("holders"),
            "hhi": None,
            "hhi_risk_level": None,
            "top_10_pct": None,
            "top_20_pct": None,
            "top_50_pct": None,
            "fake_diversification_score": None,
            "exchange_funded_pct": None,
            "new_wallets_pct": None,
            "warnings": None,
        },
        "wallet_metrics": None,
        "funding_analysis": {"deployer_funded_by": body.get("deployer_funded_by")},
        "holder_overlap": None,
        "liquidity_verification": None,
        "deployer_profile": None,
        "price_usd": body.get("price_usd"),
        "market_cap_usd": None,
        "liquidity_usd": body.get("liquidity_usd"),
        "volume_24h_usd": body.get("volume_24h"),
        "age_hours": None,
        "news_sentiment": None,
        "news_card": None,
        "rag_context": None,
        "data_sources": None,
        "sentinel_premium_preview": None,
    }


def _flatten_dexscreener(address: str, chain: str) -> dict[str, Any]:
    """Fallback scan shape from DexScreener pairs (risk fields unavailable)."""
    return {
        "source": "dexscreener:/latest/dex/tokens",
        "status": "ok",
        "scanned_at": None,
        "cached": False,
        "tier": "free",
        "background_status": "complete",
        "fallback_used": True,
        "modules_run": ["dexscreener_fallback"],
        "scan_duration_ms": None,
        "safety_score": None,
        "risk_score": None,
        "risk_level": "unavailable",
        "confidence": None,
        "risk_flags": [],
        "green_flags": [],
        "breakdown": None,
        "honeypot": "unknown",
        "security_checks": {},
        "bundle_analysis": None,
        "wash_trading": _wash_trading_block({}),
        "holder_analysis": None,
        "wallet_metrics": None,
        "funding_analysis": None,
        "holder_overlap": None,
        "liquidity_verification": None,
        "deployer_profile": None,
        "deployer_address": None,
        "dev_wallet": None,
        "socials": {},
        "price_usd": None,
        "market_cap_usd": None,
        "liquidity_usd": None,
        "volume_24h_usd": None,
        "age_hours": None,
        "news_sentiment": None,
        "news_card": None,
        "rag_context": None,
        "data_sources": None,
        "sentinel_premium_preview": None,
    }


def v2_token_to_legacy(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a /api/v2/scanner/token/scan response into the legacy
    report dict shape used by format_scan_report(), preserving the new
    explainable-scoring fields (safety_score, confidence, risk_level,
    green_flags, breakdown, evidence, modules_run)."""
    evidence = data.get("evidence") or {}
    holders = evidence.get("holders") or {}
    liquidity = evidence.get("liquidity") or {}
    security = evidence.get("security") or {}
    security.get("checks") or {}
    dev = evidence.get("deployer") or {}
    mkt = evidence.get("market") or {}
    funding = evidence.get("funding_analysis") or {}
    overlap = evidence.get("holder_overlap") or {}
    risks = list(data.get("risk_flags") or [])
    green = list(data.get("green_flags") or [])
    safety = _safe_float(data.get("safety_score"))

    # Deployer address: backend dev-reputation evidence first, then the
    # SENTINEL metadata-fingerprint ``creator`` (Pump.fun creator — the free
    # tier runs metadata_fingerprint even though the deployer reputation
    # module is PRO-only, which is why /scan used to show "unknown").
    deployer_addr = str(dev.get("deployer_address") or dev.get("address") or "").strip()
    if not deployer_addr or deployer_addr.lower() in _EMPTY_ADDR:
        _sent = data.get("sentinel")
        _sfree = _sent.get("free") if isinstance(_sent, dict) else None
        _mf = _sfree.get("metadata_fingerprint") if isinstance(_sfree, dict) else None
        if isinstance(_mf, dict):
            deployer_addr = str(_mf.get("creator") or "").strip()
    deployer_addr = deployer_addr if deployer_addr.lower() not in _EMPTY_ADDR else ""

    # ── Real market data (fix 2026-08-16) ──
    # The v2 payload carries the oracle's market snapshot when a tier provided
    # it. Liquidity falls back to ``evidence.liquidity.liquidity_usd`` (the
    # SENTINEL liquidity module), then to None — never a fabricated 0.
    liquidity_usd: float | None = _safe_float(data.get("liquidity_usd"))
    if not liquidity_usd:
        ev_liq = evidence.get("liquidity")
        if isinstance(ev_liq, dict):
            liquidity_usd = _safe_float(ev_liq.get("liquidity_usd"))
    if not liquidity_usd:
        liquidity_usd = None

    # Multi-timeframe price change / volume. The backend carries a compact
    # ``market`` map when the price oracle tier provided it (see
    # engines/_price_adapters.py DexScreener tier); the bot's DexScreener
    # supplement fills these keys when the backend did not. Absent fields
    # stay None — the formatter renders "—", never 0.
    _market = data.get("market")
    mkt_tf = _market if isinstance(_market, dict) else {}
    _pc = mkt_tf.get("price_change")
    price_change_tf = _pc if isinstance(_pc, dict) else {}
    _vol = mkt_tf.get("volume")
    volume_tf = _vol if isinstance(_vol, dict) else {}
    pc_1h = price_change_tf.get("h1") if price_change_tf.get("h1") is not None else None
    pc_6h = price_change_tf.get("h6") if price_change_tf.get("h6") is not None else None
    pc_24h = price_change_tf.get("h24") if price_change_tf.get("h24") is not None else None

    # Holders: own evidence first, then GMGN enrichment (holder_count — the
    # normalized shape from gmgn_enrich._normalize_holders), then the SENTINEL
    # holder_analysis total. None when no provider reported a count.
    gmgn_holders = data.get("gmgn", {}).get("holders") if isinstance(data.get("gmgn"), dict) else None
    sent_free = data.get("sentinel", {}).get("free") if isinstance(data.get("sentinel"), dict) else None
    sent_ha = sent_free.get("holder_analysis") if isinstance(sent_free, dict) else None
    holder_count = holders.get("holder_count")
    if not holder_count and isinstance(gmgn_holders, dict):
        holder_count = gmgn_holders.get("holder_count") or gmgn_holders.get("total_holders")
    if not holder_count and isinstance(sent_ha, dict):
        holder_count = sent_ha.get("total_holders")
    if holder_count is None:
        holder_count = None
    else:
        try:
            holder_count = int(float(holder_count))
        except (TypeError, ValueError):
            holder_count = None

    return {
        "address": data.get("token", ""),
        "chain": data.get("chain", "solana"),
        "name": data.get("name") or "Unknown",
        "symbol": data.get("symbol") or "???",
        "price": _safe_float(data.get("price_usd")) or None,
        "mcap": _safe_float(data.get("market_cap_usd")) or None,
        "fdv": _safe_float(data.get("market_cap_usd")) or None,
        "volume_24h": _safe_float(data.get("volume_24h_usd")) or None,
        "liquidity": liquidity_usd,
        "price_change_1h": pc_1h,
        "price_change_6h": pc_6h,
        "price_change_24h": pc_24h,
        "price_change_7d": None,
        "volume_1h": volume_tf.get("h1"),
        "volume_6h": volume_tf.get("h6"),
        "holders": holder_count,
        "pair_address": "",
        "dex": "",
        "created_at": None,
        "risk_score": round(100 - safety, 1),
        "threats": risks,
        "contract_verified": None,
        "honeypot": None,
        "buy_tax": None,
        "sell_tax": None,
        "lp_locked": (
            _safe_float(liquidity.get("locked_pct")) / 100 if liquidity.get("locked_pct") is not None else None
        ),
        "mintable": None,
        "can_blacklist": None,
        "owner_renounced": None,
        "top_holders": [],
        "bundle_detected": "bundle" in " ".join(risks).lower(),
        # NOTE: "fresh_wallet_pct" is set once, below, from the v2 funding
        # analysis (the legacy converter's duplicate 0-default was dead code —
        # the funding value always won in dict order).
        "exchange_pct": 0,
        # NOTE: the legacy scanner (tools/scanner/_scans.py) computes a real
        # volume/mcap ratio here; the v2 converter used to mis-map the volume-
        # authenticity engine's fake_volume_pct (0-100) into this ratio slot,
        # which corrupted the "Vol/MCap ratio" line. The v2 path now carries
        # the wash verdict in ``wash_trading`` instead and leaves this None.
        "volume_real_ratio": None,
        "dev_wallet": deployer_addr or None,
        "dev_other_tokens": [],
        "errors": [],
        # Socials: the backend v2 payload may carry a ``socials`` map (the
        # bot's DexScreener supplement fills it when the backend does not).
        "socials": data.get("socials") if isinstance(data.get("socials"), dict) else {},
        # Resolved deployer address (evidence + SENTINEL metadata creator)
        "deployer_address": deployer_addr or None,
        # v2 explainable scoring
        "safety_score": safety,
        "confidence": _safe_float(data.get("confidence")),
        "risk_level": data.get("risk_level") or "unknown",
        "green_flags": green,
        "breakdown": data.get("breakdown"),
        "evidence": evidence,
        "modules_run": list(data.get("modules_run") or []),
        # v2 funding analysis (exchange-funded / fresh wallets / scam overlap)
        "exchange_funded_pct": _safe_float(funding.get("exchange_funded_pct")),
        "fresh_wallet_pct": _safe_float(funding.get("fresh_wallet_pct")),
        "holder_overlap_pct": _safe_float(overlap.get("max_overlap_pct")),
        # v2 SENTINEL phase (free/premium module buckets) — surfaced as
        # "SENTINEL" flags on /scan (see formatters.format_sentinel_flags).
        "sentinel": data.get("sentinel"),
        # v2 news sentiment (RugBrain news_v2)
        "news_sentiment": data.get("news_sentiment"),
        # v2 own-data intel (bundle launch detection, wallet freshness
        # metrics, repeat-deployer track record) — surfaced as the
        # "Advanced Analysis" / banner blocks on /scan. The backend exposes
        # these at the top level of the v2 payload; degrade gracefully when
        # absent (legacy or older backend).
        "bundle_analysis": data.get("bundle_analysis"),
        # v2 wash-trading verdict (own volume-authenticity engine) — the
        # dedicated 📉 Fake Volume line on /scan + /deepscan reads this block.
        "wash_trading": _wash_trading_block(mkt),
        "wallet_metrics": data.get("wallet_metrics"),
        "deployer_profile": (
            data.get("deployer_profile")
            or (data.get("deep_profile") or {}).get("deployer_profile")
            or (evidence.get("deployer") or {}).get("deployer_profile")
        ),
        # v2 GMGN holder/smart-money enrichment (attached by
        # scan_token_v2 via enrich_scan_with_gmgn) — surfaced as the
        # "Smart Money / Holders" section on /scan.
        "gmgn": data.get("gmgn"),
    }


def v2_wallet_to_legacy(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a /api/v2/scanner/wallet/scan response into the legacy
    report dict shape used by format_wallet_report(), preserving the new
    v2 intel fields (entity_name, entity_type, persona, scam exposure,
    smart money overlap, behavior patterns, risk forecast)."""
    return {
        "address": data.get("address", ""),
        "chain": data.get("chain", "solana"),
        "balance_usd": _safe_float(data.get("total_value_usd")),
        "tx_count": int(data.get("token_count") or 0),
        "first_seen": None,
        "wallet_age_days": 0,
        "top_tokens": [],
        "is_fresh": False,
        "is_exchange": (data.get("entity_type") or "").lower() == "exchange",
        "is_contract": (data.get("entity_type") or "").lower() == "contract",
        "risk_score": _safe_float(data.get("risk_score")),
        "flags": list(data.get("risk_flags") or []),
        "recent_txs": [],
        "funding_source": None,
        "connected_wallets": [],
        "pnl_estimate": None,
        "errors": [],
        # v2 wallet intel
        "labels": list(data.get("labels") or []),
        "entity_name": data.get("entity_name") or "",
        "entity_type": data.get("entity_type") or "",
        "persona": data.get("persona") or "",
        "persona_confidence": int(data.get("persona_confidence") or 0),
        "scam_tokens_held": int(data.get("scam_tokens_held") or 0),
        "scam_exposure_pct": _safe_float(data.get("scam_exposure_pct")),
        "smart_money_overlap": list(data.get("smart_money_overlap") or []),
        "behavior_patterns": list(data.get("behavior_patterns") or []),
        "risk_forecast": data.get("risk_forecast") or "",
        "modules_run": list(data.get("modules_run") or []),
        # v2 funding analysis — the wallet's exposure to scams IS its
        # holder-overlap-with-known-scams metric. Exchange/fresh percentages
        # are carried through defensively when the backend provides them.
        "exchange_funded_pct": data.get("exchange_funded_pct"),
        "fresh_wallet_pct": data.get("fresh_wallet_pct"),
        "holder_overlap_pct": _safe_float(data.get("scam_exposure_pct")),
    }
