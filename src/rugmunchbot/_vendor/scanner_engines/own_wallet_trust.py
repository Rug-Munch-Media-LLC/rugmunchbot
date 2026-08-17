# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/own_wallet_trust.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Own Wallet Trust — 0-100 trust score + one-line verdict from own data
=====================================================================
Intelligent wallet forensics: scores a wallet's trustworthiness (0-100,
higher = more trustworthy) from available signals and renders a one-line
verdict like::

    🟢 Trust 78/100 — aged, CEX-funded, no prior rugs
    🔴 Trust 12/100 — fresh, mixer-funded, linked to 3 rugs

Signals (honest — each only counts when its data is actually present):

  - **Age** (first on-chain tx age)          — 0-20 pts
  - **Activity** (transfer count)            — 0-15 pts
  - **Funding source** (CEX / mixer / fresh / unknown) — 0-25 pts
  - **Prior scam/rug association** (deployer_history
    ``is_scam_related`` rows for this wallet) — 0-25 pts
  - **PnL / win-rate** (only when the caller passes GMGN/backend intel) — 0-10 pts
  - **Exchange-vs-OTC labeling** — a wallet that IS a known CEX hot
    wallet is special-cased to Trust 90 ("exchange wallet")
  - **Wash / sniper patterns** (intel-only penalties)

Penalties: sanctioned / known-scam wallet labels (-30) and wash-trading /
sniper behavior from intel (-20).

Data Sources (own ClickHouse first, caller context as supplement):
  Primary: wallet_memory.evm_transfers (stats + first funder) and
           wallet_memory.deployer_history (rug association)
  Fallback: caller-supplied ``context`` (wallet_age_days / tx_count /
            funding_source / is_exchange / pnl_estimate) and ``intel``
            (gmgn win_rate / wash / sniper scores)

Fail-open by design: when NO signal resolves (no ClickHouse data, no
context, no intel) the payload renders ``trust_score: None`` and line
``"—"``. A missing signal is never scored as trustworthy or risky — it is
just absent. Never fabricates verdicts for missing data.

Usage:
    from rugmunchbot._vendor.scanner_engines.own_wallet_trust import get_wallet_trust

    result = await get_wallet_trust("0x...", "ethereum", context=wallet_report)
    # result = {
    #     "check": "wallet_trust",
    #     "result": "scanned" | "unavailable",
    #     "trust_score": 78,          # None when unavailable
    #     "trust_level": "high" | "medium" | "low" | None,
    #     "trust_line": "🟢 Trust 78/100 — aged, CEX-funded, no prior rugs",
    #     "signals": ["aged", "CEX-funded", "no prior rugs"],
    #     "factors": {
    #         "age_days": 812, "age_score": 20,
    #         "tx_count": 4521, "activity_score": 15,
    #         "funding_source": "cex", "funding_label": "Binance",
    #         "funding_score": 25,
    #         "rug_count": 0, "deployments": 4, "rug_score": 25,
    #         "win_rate": 0.62, "pnl_score": 10,
    #         "penalties": 0,
    #     },
    #     "confidence": 0.9,
    # }
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)
from rugmunchbot._vendor.scanner_engines._funding_queries import (
    _query_wallet_first_funder,
    _query_wallet_stats,
)
from rugmunchbot._vendor.scanner_engines.own_wallet_map import (
    _classify_funder_cex,
    _classify_funder_mixer,
    _is_fresh_funder,
)

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_TRUST_CACHE: dict[str, dict[str, Any]] = {}
_TRUST_CACHE_TTL = 300  # 5 minutes — wallet history changes slowly
_TRUST_CACHE_MAX = 200

# ── Age scoring: (min_age_days, score) — first matching band wins ────────
_AGE_BANDS: tuple[tuple[int, int], ...] = (
    (730, 20),  # >= 2 years
    (365, 18),  # >= 1 year
    (180, 15),  # >= 6 months
    (90, 12),  # >= 3 months
    (30, 8),  # >= 1 month
    (7, 4),  # >= 1 week
    (0, 0),  # fresh
)
# ── Activity scoring: (min_tx_count, score) ──────────────────────────────
_ACTIVITY_BANDS: tuple[tuple[int, int], ...] = (
    (1000, 15),
    (100, 12),
    (30, 9),
    (10, 6),
    (1, 3),
    (0, 0),
)
# ── Funding scoring ───────────────────────────────────────────────────────
_FUNDING_SCORE_CEX = 25
_FUNDING_SCORE_UNKNOWN = 12  # honest neutral — an unlabeled funder is not proof of risk
_FUNDING_SCORE_FRESH = 0
_FUNDING_SCORE_MIXER = 0

# ── Prior-rug scoring (deployer_history is_scam_related) ─────────────────
_RUG_SCORE_NO_HISTORY = 20  # neutral: no deploy history seen — not evidence of trust
_RUG_SCORE_CLEAN = 25  # deployments exist, zero scam-related
_RUG_SCORE_ONE = 10
_RUG_SCORE_MANY = 0

# ── Intel (PnL / win-rate) scoring — only when caller supplies intel ─────
_PNL_SCORE_MAX = 10

# ── Penalties ─────────────────────────────────────────────────────────────
_PENALTY_SCAM_LABEL = 30
_PENALTY_WASH_SNIPER = 20

# ── Exchange special case ─────────────────────────────────────────────────
_EXCHANGE_TRUST = 90

#: Thresholds for the 🟢 / 🟡 / 🔴 bucket + human trust level.
_HIGH_TRUST = 70
_MED_TRUST = 40

#: A caller-provided wallet_age_days of 0 is ambiguous (unknown / hardcoded
#: default in v2->legacy converters) — never score it as "born today".
_MIN_MEANINGFUL_AGE_DAYS = 1


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_wallet_trust(
    wallet: str,
    chain: str,
    *,
    context: dict[str, Any] | None = None,
    intel: dict[str, Any] | None = None,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Compute a 0-100 trust score + one-line verdict for a wallet.

    Args:
        wallet: Wallet address (lowercased internally).
        chain: Chain name (ethereum / bsc / solana / ...).
        context: Optional caller-supplied scan facts — keys honored:
            ``wallet_age_days`` (>0 only), ``tx_count`` (>0 only),
            ``funding_source`` (str), ``is_exchange`` (bool),
            ``entity_type`` (str), ``pnl_estimate`` (number).
        intel: Optional behavior intel (e.g. gmgn ``get_wallet_intelligence``)
            — keys honored: ``win_rate``, ``pnl.realized``,
            ``wash_trading_score``, ``sniper_score``.
        force_fresh: Bypass the in-process cache.

    Returns:
        Dict with ``trust_score`` (0-100 int) / ``trust_line`` (one-line
        verdict) / ``signals`` / ``factors``. Fail-open to
        ``result: "unavailable"`` + ``trust_score: None`` + ``line "—"``
        when no signal resolves.
    """
    wallet = (wallet or "").lower().strip()
    chain_norm = (chain or "").lower().strip()
    _start = time.monotonic()

    if not wallet or not chain_norm:
        return _unavailable_result(wallet, chain_norm, reason="wallet and chain are required")

    cache_key = f"{wallet}:{chain_norm}:{_context_hash(context, intel)}"
    if not force_fresh and cache_key in _TRUST_CACHE:
        cached = _TRUST_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _TRUST_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    # ── 1. Own ClickHouse signals (fail-open to {}) ──
    stats = await _fetch_stats(wallet, chain_norm)
    funder = await _fetch_funder(wallet, chain_norm)
    rug_stats = await _fetch_rug_stats(wallet)
    own_labels = await _fetch_own_labels(wallet)

    # ── 2. Merge with caller context (context fills gaps; own data wins) ──
    ctx = context or {}
    age_days = stats.get("age_days")
    if age_days is None and int(ctx.get("wallet_age_days") or 0) >= _MIN_MEANINGFUL_AGE_DAYS:
        age_days = int(ctx["wallet_age_days"])
    tx_count = stats.get("tx_count")
    if tx_count is None and int(ctx.get("tx_count") or 0) > 0:
        tx_count = int(ctx["tx_count"])

    # ── 3. Funding source classification ──
    funding_source: str | None = None
    funding_label: str | None = None
    if funder:
        cex = _classify_funder_cex(funder, chain_norm)
        if cex:
            funding_source, funding_label = "cex", cex
        else:
            mixer = _classify_funder_mixer(funder, chain_norm)
            if mixer:
                funding_source, funding_label = "mixer", str(mixer)
            elif await _is_fresh_funder(funder, chain_norm):
                funding_source, funding_label = "fresh", "fresh"
            else:
                funding_source, funding_label = "unknown", "unknown"
    elif isinstance(ctx.get("funding_source"), str) and ctx["funding_source"]:
        raw = str(ctx["funding_source"]).lower()
        if raw in ("cex", "exchange", "binance", "coinbase", "kraken", "okx", "bybit", "kucoin", "gate.io", "mexc"):
            funding_source, funding_label = "cex", str(ctx["funding_source"])
        elif "mixer" in raw or "tornado" in raw:
            funding_source, funding_label = "mixer", str(ctx["funding_source"])
        elif raw in ("fresh", "new"):
            funding_source, funding_label = "fresh", "fresh"
        else:
            funding_source, funding_label = "unknown", str(ctx["funding_source"])

    # ── 4. Exchange-vs-OTC labeling ──
    is_exchange = bool(ctx.get("is_exchange")) or str(ctx.get("entity_type") or "").lower() == "exchange"
    own_entity = own_labels.get("entity_type")
    if own_entity in ("exchange", "cex_hot_wallet"):
        is_exchange = True
        funding_source, funding_label = "cex", own_labels.get("entity_name") or "CEX"

    # ── 5. Prior scam / rug association ──
    rug_count = rug_stats.get("scams") if rug_stats else None
    deployments = rug_stats.get("total") if rug_stats else None
    if rug_count is None and ctx:
        # caller-provided scam exposure is a weaker proxy — only used to
        # avoid claiming "no prior rugs" when the caller saw scam tokens.
        exposure = ctx.get("scam_exposure_pct")
        if exposure is not None and float(exposure) > 0:
            rug_count = max(1, int(ctx.get("scam_tokens_held") or 1))

    # ── 6. Intel (PnL / win-rate / wash / sniper) — optional ──
    win_rate: float | None = None
    wash_score: float = 0.0
    sniper_score: float = 0.0
    if intel:
        try:
            win_rate = float(intel.get("win_rate") or 0)
        except (TypeError, ValueError):
            win_rate = None
        wash_score = _to_float(intel.get("wash_trading_score"))
        sniper_score = _to_float(intel.get("sniper_score"))

    # ── 7. Score ──
    payload = _score_wallet(
        age_days=age_days,
        tx_count=tx_count,
        funding_source=funding_source,
        funding_label=funding_label,
        rug_count=rug_count,
        deployments=deployments,
        win_rate=win_rate,
        wash_score=wash_score,
        sniper_score=sniper_score,
        is_exchange=is_exchange,
        own_entity=own_entity,
        own_scam=own_labels.get("is_scam"),
        own_sanctioned=own_labels.get("is_sanctioned"),
        pnl_estimate=ctx.get("pnl_estimate"),
    )

    if payload["result"] == "unavailable":
        return payload

    _cache_trust(cache_key, payload)
    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_wallet_trust_latency",
        ms=round(_elapsed_ms, 1),
        wallet=wallet[:10],
        chain=chain_norm,
        trust=payload.get("trust_score"),
    )
    return payload


# ═══════════════════════════════════════════════════════════════════════════
# DATA ACCESS (own ClickHouse, fail-open)
# ═══════════════════════════════════════════════════════════════════════════


async def _fetch_stats(wallet: str, chain_norm: str) -> dict[str, Any]:
    """First-seen timestamp + transfer count for a wallet (EVM)."""
    try:
        rows = await run_sync(_query_wallet_stats, wallet, chain_norm)
        if rows and rows[0]:
            first_seen = rows[0][0]
            total = rows[0][1]
            age_days = _age_days_from_ts(first_seen)
            return {"age_days": age_days, "tx_count": int(total or 0) if total else None}
    except Exception as exc:
        logger.debug("wallet_trust_stats_failed: %s", exc)
    return {}


async def _fetch_funder(wallet: str, chain_norm: str) -> str | None:
    """The wallet's first-ever incoming funder (EVM)."""
    try:
        rows = await run_sync(_query_wallet_first_funder, wallet, chain_norm)
        if rows and rows[0] and rows[0][0]:
            return str(rows[0][0]).lower()
    except Exception as exc:
        logger.debug("wallet_trust_funder_failed: %s", exc)
    return None


async def _fetch_rug_stats(wallet: str) -> dict[str, Any] | None:
    """Deployer-history stats: total deployments + scam-related count."""
    try:
        rows = await run_sync(_query_deployer_scam_stats, wallet)
        if rows and rows[0] and rows[0][0] is not None:
            return {"total": int(rows[0][0]), "scams": int(rows[0][1] or 0)}
    except Exception as exc:
        logger.debug("wallet_trust_rug_stats_failed: %s", exc)
    return None


async def _fetch_own_labels(wallet: str) -> dict[str, Any]:
    """Entity registry (CEX/mixer/scam) + wallet_labels sanction flags."""
    out: dict[str, Any] = {}
    try:
        from app.entity_registry import (
            ENTITY_TYPE_CEX_HOT_WALLET,
            ENTITY_TYPE_EXCHANGE,
            ENTITY_TYPE_KNOWN_SCAM,
            ENTITY_TYPE_MIXER,
            get_entity_registry,
        )

        match = get_entity_registry().classify_address(wallet)
        if match:
            out["entity_type"] = match.entity_type
            out["entity_name"] = match.entity_name
            if match.entity_type in (ENTITY_TYPE_EXCHANGE, ENTITY_TYPE_CEX_HOT_WALLET):
                out["is_exchange"] = True
            if match.entity_type == ENTITY_TYPE_MIXER:
                out["is_mixer"] = True
            if match.entity_type == ENTITY_TYPE_KNOWN_SCAM:
                out["is_scam"] = True
    except Exception as exc:
        logger.debug("wallet_trust_entity_failed: %s", exc)
    try:
        from rugmunchbot._vendor.scanner_engines._common import _query_batch_labels

        rows = await run_sync(_query_batch_labels, [wallet])
        for row in rows or []:
            if len(row) >= 6 and str(row[0]).lower() == wallet:
                if row[5]:  # is_sanctioned
                    out["is_sanctioned"] = True
                cat = str(row[2] or "").lower()
                if cat in ("scam", "scammer", "fraud", "honeypot", "rug"):
                    out["is_scam"] = True
    except Exception as exc:
        logger.debug("wallet_trust_labels_failed: %s", exc)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════════════════


def _score_wallet(
    *,
    age_days: int | None,
    tx_count: int | None,
    funding_source: str | None,
    funding_label: str | None,
    rug_count: int | None,
    deployments: int | None,
    win_rate: float | None,
    wash_score: float,
    sniper_score: float,
    is_exchange: bool,
    own_entity: str | None,
    own_scam: bool | None,
    own_sanctioned: bool | None,
    pnl_estimate: Any,
) -> dict[str, Any]:
    """Deterministic trust score + one-line verdict from resolved signals."""
    # ── Special case: the wallet IS a known exchange hot wallet ──
    if is_exchange:
        signals = ["exchange wallet"]
        return {
            "check": "wallet_trust",
            "result": "scanned",
            "trust_score": _EXCHANGE_TRUST,
            "trust_level": "high",
            "trust_line": f"\U0001f7e2 Trust {_EXCHANGE_TRUST}/100 \u2014 exchange wallet",
            "signals": signals,
            "factors": {"funding_source": "cex", "exchange": True},
            "confidence": 0.95,
        }

    factors: dict[str, Any] = {}
    available = 0  # number of distinct signal groups with real data
    penalties = 0

    # ── Age (0-20) ──
    age_score: int | None = None
    if age_days is not None and age_days >= 0:
        available += 1
        for min_days, score in _AGE_BANDS:
            if age_days >= min_days:
                age_score = score
                break
        factors["age_days"] = age_days
        factors["age_score"] = age_score

    # ── Activity (0-15) ──
    activity_score: int | None = None
    if tx_count is not None and tx_count >= 0:
        available += 1
        for min_count, score in _ACTIVITY_BANDS:
            if tx_count >= min_count:
                activity_score = score
                break
        factors["tx_count"] = tx_count
        factors["activity_score"] = activity_score

    # ── Funding (0-25) ──
    funding_score: int | None = None
    if funding_source is not None:
        available += 1
        if funding_source == "cex":
            funding_score = _FUNDING_SCORE_CEX
        elif funding_source == "unknown":
            funding_score = _FUNDING_SCORE_UNKNOWN
        elif funding_source == "fresh":
            funding_score = _FUNDING_SCORE_FRESH
        elif funding_source == "mixer":
            funding_score = _FUNDING_SCORE_MIXER
        factors["funding_source"] = funding_source
        factors["funding_label"] = funding_label

    # ── Prior scam / rug association (0-25) ──
    rug_score: int | None = None
    if rug_count is not None:
        available += 1
        if deployments is None or deployments == 0:
            rug_score = _RUG_SCORE_NO_HISTORY
        elif rug_count == 0:
            rug_score = _RUG_SCORE_CLEAN
        elif rug_count == 1:
            rug_score = _RUG_SCORE_ONE
        else:
            rug_score = _RUG_SCORE_MANY
        factors["rug_count"] = rug_count
        factors["deployments"] = deployments
        factors["rug_score"] = rug_score

    # ── PnL / win-rate (0-10, intel only) ──
    pnl_score: int | None = None
    if win_rate is not None:
        available += 1
        if win_rate >= 0.6:
            pnl_score = _PNL_SCORE_MAX
        elif win_rate >= 0.4:
            pnl_score = 6
        else:
            pnl_score = 2
        factors["win_rate"] = win_rate
        factors["pnl_score"] = pnl_score
    elif pnl_estimate is not None:
        try:
            pnl = float(pnl_estimate)
        except (TypeError, ValueError):
            pnl = 0.0
        available += 1
        pnl_score = _PNL_SCORE_MAX if pnl >= 0 else 0
        factors["pnl_estimate"] = round(pnl, 2)
        factors["pnl_score"] = pnl_score

    # ── Penalties ──
    if own_scam:
        penalties += _PENALTY_SCAM_LABEL
        factors["scam_label"] = True
    if own_sanctioned:
        penalties += _PENALTY_SCAM_LABEL
        factors["sanctioned"] = True
    if wash_score >= 50:
        penalties += _PENALTY_WASH_SNIPER
        factors["wash_trading"] = True
    if sniper_score >= 50:
        penalties += _PENALTY_WASH_SNIPER
        factors["sniper_pattern"] = True

    # ── Honesty gate: no real signals → "—" ──
    if available == 0:
        return _unavailable_result(None, None, reason="no wallet signals available")

    factors["penalties"] = penalties
    raw = sum(s for s in (age_score, activity_score, funding_score, rug_score, pnl_score) if s is not None)
    trust_score = max(0, min(100, raw - penalties))

    signals = _build_signals(
        age_days=age_days,
        funding_source=funding_source,
        funding_label=funding_label,
        rug_count=rug_count,
        deployments=deployments,
        win_rate=win_rate,
        wash_score=wash_score,
        sniper_score=sniper_score,
        own_scam=own_scam,
        own_sanctioned=own_sanctioned,
    )
    level = "high" if trust_score >= _HIGH_TRUST else "medium" if trust_score >= _MED_TRUST else "low"

    return {
        "check": "wallet_trust",
        "result": "scanned",
        "trust_score": trust_score,
        "trust_level": level,
        "trust_line": _render_trust_line(trust_score, signals),
        "signals": signals,
        "factors": factors,
        "confidence": _confidence(available),
    }


def _build_signals(
    *,
    age_days: int | None,
    funding_source: str | None,
    funding_label: str | None,
    rug_count: int | None,
    deployments: int | None,
    win_rate: float | None,
    wash_score: float,
    sniper_score: float,
    own_scam: bool | None,
    own_sanctioned: bool | None,
) -> list[str]:
    """One-line verdict attributes — ordered most-informative first."""
    signals: list[str] = []

    if age_days is not None:
        if age_days < 30:
            signals.append("fresh")
        elif age_days < 365:
            signals.append(f"{age_days // 30}mo old")
        else:
            signals.append("aged")

    if funding_source == "cex":
        signals.append(f"CEX-funded{(' (' + str(funding_label) + ')') if funding_label else ''}")
    elif funding_source == "mixer":
        signals.append("mixer-funded")
    elif funding_source == "fresh":
        signals.append("fresh-funded")
    elif funding_source == "unknown":
        signals.append("unknown funding")

    if rug_count is not None:
        if rug_count > 0:
            signals.append(f"linked to {rug_count} rug{'s' if rug_count != 1 else ''}")
        elif deployments:
            signals.append("no prior rugs")
        # deployments absent → neutral, no claim

    if win_rate is not None:
        signals.append(f"win-rate {round(win_rate * 100)}%")
    if wash_score >= 50:
        signals.append("wash-trading")
    if sniper_score >= 50:
        signals.append("sniper pattern")
    if own_scam:
        signals.append("scam-labeled")
    if own_sanctioned:
        signals.append("sanctioned")

    return signals or ["no signals"]


def _render_trust_line(trust_score: int, signals: list[str]) -> str:
    """``🟢 Trust 78/100 — aged, CEX-funded, no prior rugs``."""
    emoji = "\U0001f7e2" if trust_score >= _HIGH_TRUST else "\U0001f7e1" if trust_score >= _MED_TRUST else "\U0001f534"
    return f"{emoji} Trust {trust_score}/100 \u2014 {', '.join(signals)}"


def _confidence(available: int) -> float:
    """Confidence from how many signal groups resolved (1.0 = all five)."""
    return round(min(1.0, 0.4 + available * 0.12), 2)


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _query_deployer_scam_stats(address: str) -> list[tuple[Any, ...]] | None:
    """Total deployments + scam-related count for a wallet (deployer_history)."""
    try:
        from rugmunchbot._vendor.lite_chain_indexer import _get_ch_client

        client = _get_ch_client()
        if client is None:
            return None
        return client.execute(
            """
            SELECT count() AS total, sum(is_scam_related) AS scams
            FROM wallet_memory.deployer_history
            WHERE deployer_address = %(addr)s
            """,
            {"addr": address.lower()},
            settings={"max_execution_time": 20},
        )
    except (ImportError, ValueError, RuntimeError, OSError) as exc:
        logger.debug("wallet_trust_deployer_query_failed: %s", exc)
        return None


def _age_days_from_ts(ts: Any) -> int | None:
    """Whole days between a first-seen timestamp and now (None on garbage)."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
    else:
        try:
            parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            naive = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except (TypeError, ValueError):
            return None
    try:
        return max(0, (datetime.now(UTC).replace(tzinfo=None) - naive).days)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _context_hash(context: dict[str, Any] | None, intel: dict[str, Any] | None) -> str:
    """Short stable key for caller-provided context (cache correctness)."""

    def _pick(d: dict[str, Any] | None, keys: tuple[str, ...]) -> str:
        if not d:
            return ""
        return "|".join(f"{k}:{d.get(k)}" for k in keys if d.get(k) is not None)

    ctx_k = _pick(context, ("wallet_age_days", "tx_count", "funding_source", "is_exchange", "pnl_estimate"))
    intel_k = _pick(intel, ("win_rate", "wash_trading_score", "sniper_score"))
    return f"{ctx_k}#{intel_k}"


def _unavailable_result(wallet: str | None, chain: str | None, *, reason: str = "") -> dict[str, Any]:
    """Fail-open payload — trust is "—" when no signal resolves."""
    return {
        "check": "wallet_trust",
        "result": "unavailable",
        "wallet": wallet,
        "chain": chain,
        "trust_score": None,
        "trust_level": None,
        "trust_line": "\u2014",
        "signals": [],
        "factors": {},
        "reason": reason,
        "confidence": 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_trust(key: str, data: dict[str, Any]) -> None:
    data["_cached_at"] = time.time()
    _TRUST_CACHE[key] = data
    if len(_TRUST_CACHE) > _TRUST_CACHE_MAX:
        _TRUST_CACHE.pop(next(iter(_TRUST_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _TRUST_CACHE,
        _TRUST_CACHE_MAX,
        _TRUST_CACHE_TTL,
        extra={"engine": "wallet_trust"},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_TRUST_CACHE)


__all__ = [
    "clear_cache",
    "get_cache_stats",
    "get_wallet_trust",
]
