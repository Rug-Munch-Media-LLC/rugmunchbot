# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/unified/scoring.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Token safety scoring — multi-dimensional, explainable risk scoring.

Extracted from ``UnifiedTokenScanner`` and evolved into the canonical 0-100
*safety* score (100 = safest, 0 = scam). Lower scores mean higher risk.

Model
-----
The unified engine is a **multi-dimensional signal aggregation** model:

1. Start at a neutral baseline of ``50`` (unknown/neutral).
2. Each signal contributes a flat penalty (risk finding) or bonus (green
   finding) that is attributed to one of five dimensions:
   ``security``, ``liquidity``, ``holders``, ``deployer``, ``market``.
3. The final safety score is ``clamp(0, 100, baseline + sum(deltas))``.
4. Every dimension is reported with its surviving points (``score`` out of
   ``max``), its net ``delta``, and the ``flags`` that caused it, so the
   final number can always be traced back to specific findings.

The five dimension weights (35/25/15/15/10) are the maximum points each
dimension can contribute toward safety and match the tiered product model
documented in ``docs/RISK_SCORING.md``::

    CORE     = security + liquidity          (60% of weight)
    DEEP     = CORE + holders + deployer     (90% of weight)
    PREMIUM  = DEEP + market                 (100% of weight)

Sibling engines
---------------
- ``app.domains.scanner.service.scoring._calculate_safety_score`` — the
  legacy module-results engine (baseline 50 + flat penalties/bonuses).
- ``app.domains.scanners.sentinel_pipeline.risk._compute_composite`` — the
  SENTINEL weighted composite, a 0-100 *risk* score (higher = riskier, the
  inverse of safety).

Confidence (0-100)
------------------
- Base confidence from modules that attempted (each module in
  ``modules_run`` gives +5, capped at 40).
- Bonus confidence for modules that returned real data (+5 to +15 depending
  on richness).
- Ensures confidence never drops to zero just because a token has limited
  data (e.g., a new token on a less-indexed chain).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rugmunchbot._vendor.unified_scanner.model import ScanResult

from rugmunchbot._vendor.scanner_engines.news_sentiment import HIGH_MEDIA_ATTENTION_THRESHOLD
from rugmunchbot._vendor.unified_scanner._evidence import build_evidence
from rugmunchbot._vendor.unified_scanner._score_helpers import apply_sentinel_scoring, build_breakdown

# Dimension weights — the maximum points each dimension can contribute
# toward the safety score. 35 + 25 + 15 + 15 + 10 = 100.
DIMENSION_WEIGHTS: dict[str, int] = {
    "security": 35,
    "liquidity": 25,
    "holders": 15,
    "deployer": 15,
    "market": 10,
}

# Neutral baseline: a token with no data at all scores exactly 50.
_BASELINE = 50.0

# Confirmed-honeypot security checks (auto_fail GoPlus / Honeypot.is).
# A confirmed honeypot is the single most decisive rug signal: the legacy
# service engine penalizes ``is_honeypot`` at +35 risk (→ 15 safety,
# "critical"). The unified engine was applying only the generic -5 per
# failed check, leaving a confirmed honeypot at 45 ("medium") — and a
# honeypot with clean-looking other modules at 65 ("low"). False negative.
HONEYPOT_PENALTY = -35.0
HONEYPOT_CHECK_IDS: frozenset[str] = frozenset({"HP01", "HP02"})

# Confirmed-honeypot override: a honeypot is the single most decisive rug
# signal, so once HONEYPOT_DETECTED fires, clean-looking checks in OTHER
# modules must not be able to lift the token back out of the critical band.
# Real-world calibration (Carbon/CSIX on BSC, GoPlus is_honeypot=1): the
# -35 penalty alone left the token at safety 29 ("high", risk 71) because
# seven passing security checks (+14) eroded the delta. The override caps
# the final safety score below the critical boundary (risk > 80) regardless
# of bonuses. Note the band mapping: safety >= 20 → "high", so the cap must
# be strictly below 20 to guarantee "critical".
HONEYPOT_OVERRIDE_MAX_SAFETY = 19.0  # safety <= 19 → risk >= 81 (critical)

# Detected bundle launch (coordinated sniper/bot buys in the first blocks
# after deployment — own ClickHouse bundle_analysis). A confirmed launch
# bundle is a decisive coordinated-launch signal, penalized in the security
# dimension (task spec: -25).
BUNDLE_PENALTY = -25.0

# Blue-chip tokens (top-100 by market cap, or >100K holders) legitimately
# carry signals the concentration/lock heuristics treat as risk: high HHI
# (a single wrapping contract can dominate supply — WBTC, wstETH) and low
# locked-LP% (liquidity sits in deep AMM pools, not locked LP positions).
# Skipping those flags for bluechips removes false HIGH/critical labels on
# tokens like WETH (Bug 1).
BLUECHIP_MARKET_CAP_USD = 1_000_000_000  # ≈ top-100 by market cap
BLUECHIP_HOLDER_COUNT = 100_000

# Hardcoded allowlist of known blue-chip tokens (mainnet addresses).
# These bypass SENTINEL concentration/low-lock/wash/honeypot heuristics even
# when market-cap / holder data is unavailable (data fetch OOMs or is rate
# limited). Keys are checksummed mainnet addresses; matched case-insensitively.
BLUECHIP_ADDRESSES: dict[str, str] = {
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2": "WETH",
    "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599": "WBTC",
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": "USDC",
    "0xdAC17F958D2ee523a2206206994597C13D831ec7": "USDT",
    "0x6B175474E89094C44Da98b954EedeAC495271d0F": "DAI",
    "0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84": "stETH",
}

# Lowercased lookup set — built once at import; addresses matched case-insensitively.
_BLUECHIP_ADDRESSES_LOWER: frozenset[str] = frozenset(a.lower() for a in BLUECHIP_ADDRESSES)


def _is_bluechip(r: ScanResult) -> bool:
    """True for known blue-chip / top-100 market-cap or >100K-holder tokens.

    The hardcoded ``BLUECHIP_ADDRESSES`` allowlist is checked FIRST so known
    blue-chips (WETH, WBTC, USDC, USDT, DAI, stETH) are recognized even when
    market-cap / holder enrichment data is unavailable (data fetch OOMs).
    Falls through to data-dependent checks (``market_cap_usd``, DataBus
    holder data, SENTINEL free-tier ``holder_analysis.total_holders``).
    Defensive: missing or malformed fields simply mean "not bluechip".
    """
    address = (r.token_address or "").strip().lower()
    if address in _BLUECHIP_ADDRESSES_LOWER:
        return True
    if float(r.market_cap_usd or 0) >= BLUECHIP_MARKET_CAP_USD:
        return True
    holders = r.holders if isinstance(r.holders, dict) else {}
    for key in ("holder_count", "total_holders"):
        try:
            if int(holders.get(key) or 0) > BLUECHIP_HOLDER_COUNT:
                return True
        except (TypeError, ValueError):
            continue
    sentinel_free = (r.sentinel or {}).get("free") or {}
    ha = sentinel_free.get("holder_analysis")
    if isinstance(ha, dict):
        try:
            if int(ha.get("total_holders") or 0) > BLUECHIP_HOLDER_COUNT:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _risk_level_from_score(score: float) -> str:
    """Map a 0-100 safety score to the canonical risk level."""
    if score >= 80:
        return "safe"
    if score >= 60:
        return "low"
    if score >= 40:
        return "medium"
    if score >= 20:
        return "high"
    return "critical"


def compute_score(r: ScanResult) -> dict[str, Any]:
    """Compute the 0-100 safety score and return an explainable breakdown.

    Mutates ``r`` in place (``safety_score``, ``confidence``, ``risk_level``,
    ``risk_flags``, ``green_flags``, ``breakdown``, ``evidence``) and returns
    the breakdown payload::

        {
            "score": 42.0,
            "risk_level": "high",
            "breakdown": {
                "security": {"score": 15.0, "max": 35, "delta": -20.0, "flags": ["CR03"]},
                "liquidity": {"score": 5.0, "max": 25, "delta": -20.0, "flags": ["LP_LOCK_LOW"]},
                ...
            },
            "evidence": {...}
        }

    Each dimension reports ``score`` (surviving points out of ``max``),
    ``delta`` (net contribution to the final score) and the flags that
    caused it. ``score = clamp(0, max, max + delta)``.
    """
    score = _BASELINE
    confidence = 0.0
    flags: list[str] = []
    green: list[str] = []

    # Blue-chip guard: wrapped/blue-chip tokens (top-100 market cap or
    # >100K holders) are not penalized for concentration/low-lock signals
    # that are structurally normal for them (Bug 1).
    bluechip = _is_bluechip(r)

    deltas: dict[str, float] = defaultdict(float)
    dim_flags: dict[str, list[str]] = defaultdict(list)

    def _apply(dim: str, delta: float, flag: str | None = None) -> None:
        """Apply a signed delta to a dimension; record the flag if any."""
        nonlocal score
        score += delta
        deltas[dim] += delta
        if flag:
            dim_flags[dim].append(flag)
            flags.append(flag)

    # ── Base confidence: modules that were attempted ──
    # Even if modules return no data, running them counts
    modules_attempted = len(r.modules_run)
    base_confidence = min(modules_attempted * 5, 40)
    confidence += base_confidence

    # ── Security checks (GoPlus / token_security) ──
    if r.security:
        checks = r.security.get("checks", {})
        if checks:
            confidence += min(len(checks) * 2, 30)
            for check_id, check in checks.items():
                status = check.get("status", "")
                if status == "fail":
                    if str(check_id).upper() in HONEYPOT_CHECK_IDS:
                        # Confirmed honeypot (buy OK / sell blocked) is the
                        # decisive rug signal — heavy penalty, not the
                        # generic -5 per failed check (calibration band:
                        # honeypot → risk > 80 regardless of other modules).
                        _apply("security", HONEYPOT_PENALTY, "HONEYPOT_DETECTED")
                    else:
                        _apply("security", -5, str(check_id))
                elif status == "pass":
                    _apply("security", 2)
                    green.append(str(check_id))
        else:
            # Security module ran but had no checks
            confidence += 10

    # ── Holder concentration ──
    if r.holders:
        h = r.holders
        top10 = float(h.get("top_10_pct", 0) or 0)
        if top10 > 80 and not bluechip:
            _apply("holders", -15, "HOLDER_CONCENTRATION_HIGH")
        elif top10 < 30:
            _apply("holders", 5)
            green.append("HOLDER_DISTRIBUTED")
        confidence += 5
    else:
        # Module attempted but no data
        confidence += 3

    # ── Holder health ──
    if r.holder_health:
        hh = r.holder_health
        gini = float(hh.get("gini", 0) or 0)
        if gini > 0.8 and not bluechip:
            _apply("holders", -10, "HOLDER_GINI_HIGH")
        confidence += 5
    else:
        confidence += 3

    # ── Clever wallet analysis (funding sources / freshness / overlap) ──
    if r.funding_analysis and isinstance(r.funding_analysis, dict):
        fa = r.funding_analysis
        exchange_pct = float(fa.get("exchange_funded_pct", 0) or 0)
        fresh_pct = float(fa.get("fresh_wallet_pct", 0) or 0)
        mixer_pct = float(fa.get("mixer_funded_pct", 0) or 0)
        if exchange_pct > 50:
            _apply("holders", -10, "HIGH_EXCHANGE_FUNDED")
        elif exchange_pct > 30:
            _apply("holders", -5, "EXCHANGE_FUNDED_ELEVATED")
        if fresh_pct > 40:
            _apply("holders", -8, "HIGH_FRESH_WALLET_PCT")
        elif fresh_pct > 20:
            _apply("holders", -4, "FRESH_WALLET_ELEVATED")
        if mixer_pct > 5:
            _apply("holders", -6, "MIXER_FUNDED_HOLDERS")
        if fa.get("holders_analyzed", 0) > 0:
            confidence += 5

    if r.wallet_freshness and isinstance(r.wallet_freshness, dict):
        wf = r.wallet_freshness
        fresh_wallet_pct = float(wf.get("fresh_wallet_pct", 0) or 0)
        if fresh_wallet_pct > 50:
            _apply("holders", -10, "HIGH_FRESH_WALLET_PCT")
        elif fresh_wallet_pct > 30:
            _apply("holders", -5, "FRESH_WALLET_ELEVATED")
        if wf.get("result") == "scanned":
            confidence += 5

    if r.holder_overlap and isinstance(r.holder_overlap, dict):
        ho = r.holder_overlap
        max_overlap = float(ho.get("max_overlap_pct", 0) or 0)
        worst_is_scam = bool(ho.get("worst_token_is_scam"))
        if worst_is_scam and max_overlap > 30:
            _apply("holders", -10, "HOLDER_OVERLAP_SCAM")
        elif max_overlap > 30:
            _apply("holders", -5, "HOLDER_OVERLAP_HIGH")
        if ho.get("result") == "scanned":
            confidence += 5

    # ── Token-level wallet freshness metrics (own engine — wallet_metrics) ──
    # fresh_wallet_pct = % of holders with wallets <30 days old; high share is
    # a farming / bot-farm signal. cex_funded_pct = % of holders funded by CEX
    # wallets; a high share reads as institutional backing (positive).
    if r.wallet_metrics and isinstance(r.wallet_metrics, dict):
        wm = r.wallet_metrics
        wm_fresh_pct = float(wm.get("fresh_wallet_pct", 0) or 0)
        wm_cex_pct = float(wm.get("cex_funded_pct", 0) or 0)
        if wm_fresh_pct > 70:
            _apply("holders", -15, "FARMING_RISK")
        if wm_cex_pct > 40:
            _apply("holders", 10)
            green.append("INSTITUTIONAL")
        if int(wm.get("holders_analyzed", 0) or 0) > 0:
            confidence += 5

    # ── Own bundle-launch analysis (own engine — bundle_analysis) ──
    # Coordinated sniper/bot buys in the first blocks after deployment is a
    # decisive rug-pattern signal: heavy security-dimension penalty.
    if r.bundle_analysis and isinstance(r.bundle_analysis, dict):
        ba = r.bundle_analysis
        if ba.get("is_bundled") or float(ba.get("launch_cluster_score", 0) or 0) >= 50:
            _apply("security", BUNDLE_PENALTY, "BUNDLE_DETECTED")
        if ba.get("result") == "scanned":
            confidence += 5

    # ── Liquidity ──
    if r.liquidity:
        liq = r.liquidity
        locked = float(liq.get("locked_pct", 0) or 0)
        if locked < 50 and not bluechip:
            _apply("liquidity", -20, "LP_LOCK_LOW")
        elif locked > 90:
            _apply("liquidity", 5)
            green.append("LP_LOCKED")
        confidence += 5
    else:
        confidence += 3

    # ── Dev reputation ──
    if r.dev_reputation:
        dev = r.dev_reputation
        risk = float(dev.get("risk_score", 0) or 0)
        if risk > 70:
            _apply("deployer", -25, "DEV_HIGH_RISK")
        elif risk < 20:
            _apply("deployer", 5)
            green.append("DEV_REPUTABLE")
        confidence += 5
    else:
        confidence += 3

    # ── Rug patterns ──
    if r.rug_patterns:
        rp = r.rug_patterns
        matches = int(rp.get("match_count", 0) or 0)
        if matches > 0:
            _apply("security", -10 * matches, f"RUG_PATTERN_MATCH:{matches}")
        confidence += 5
    else:
        confidence += 3

    # ── Volume authenticity ──
    if r.volume_auth:
        va = r.volume_auth
        fake_pct = float(va.get("fake_volume_pct", 0) or 0)
        if fake_pct > 50:
            _apply("market", -20, "VOLUME_FAKE")
        elif fake_pct < 10:
            _apply("market", 5)
            green.append("VOLUME_AUTHENTIC")
        confidence += 5
    else:
        confidence += 3

    # ── Chain-indexer pattern detection ──
    if r.lite_chain_indexer_patterns:
        cip = r.lite_chain_indexer_patterns
        findings = cip.get("findings", cip.get("data", {}).get("findings", []))
        if findings:
            total_risk = sum(f.get("risk_score", 0) for f in findings)
            _apply("market", -min(total_risk, 40))
            for f in findings:
                pattern = f.get("pattern", "unknown")
                if f.get("detected"):
                    _apply("market", 0, f"ONCHAIN_{pattern.upper()}")
            confidence += min(len(findings) * 3, 10)
        else:
            confidence += 5

    # ── Premium detections ──
    if r.premium:
        premium_flags = 0
        for feature, data in r.premium.items():
            if data and isinstance(data, dict):
                premium_risk = data.get("risk", data.get("risk_level", ""))
                if premium_risk in ("HIGH", "CRITICAL"):
                    _apply("market", -10, f"PREMIUM_{feature.upper()}")
                    premium_flags += 1
        confidence += min(premium_flags * 5, 15)

    # ── SENTINEL deep-forensic findings (Phase 5) ──
    # SENTINEL free-tier modules report alongside the DataBus chains; their
    # findings feed the SAME five dimensions so a critical deep-forensic
    # signal moves the unified score (not just the SENTINEL report). A
    # ``sentinel_boost`` is applied when two or more modules independently
    # flag critical/high risk — correlated deep-forensic signals reinforce
    # each other.
    if r.sentinel and isinstance(r.sentinel, dict):
        confidence += apply_sentinel_scoring(r, bluechip=bluechip, apply=_apply)

    # ── Clamp ──
    r.safety_score = max(0.0, min(100.0, score))
    r.confidence = min(confidence, 100.0)

    # ── Confirmed-honeypot override ──
    # A confirmed honeypot (HP01/HP02 auto-fail) is decisive: cap the final
    # safety score at the critical boundary so green bonuses in other
    # modules cannot mask it. See HONEYPOT_OVERRIDE_MAX_SAFETY above.
    if "HONEYPOT_DETECTED" in flags:
        r.safety_score = min(r.safety_score, HONEYPOT_OVERRIDE_MAX_SAFETY)

    # ── Risk level ──
    r.risk_level = _risk_level_from_score(r.safety_score)

    r.risk_flags = flags
    r.green_flags = green

    # ── Explainable per-dimension breakdown ──
    breakdown = build_breakdown(deltas, dim_flags, DIMENSION_WEIGHTS)

    evidence = build_evidence(r)
    r.breakdown = breakdown
    r.evidence = evidence

    return {
        "score": r.safety_score,
        "risk_level": r.risk_level,
        "breakdown": breakdown,
        "evidence": evidence,
    }


# ── News-driven risk adjustment (post-scoring enrichment) ─────────────


def _apply_news_delta(r: ScanResult, delta: float, flag: str, *, risk: bool) -> None:
    """Apply a news delta to the safety score, flags, and market dimension."""
    r.safety_score = max(0.0, min(100.0, r.safety_score + delta))
    if risk:
        if flag not in r.risk_flags:
            r.risk_flags.append(flag)
    else:
        if flag not in r.green_flags:
            r.green_flags.append(flag)

    bd = r.breakdown
    if isinstance(bd, dict):
        market = bd.get("market")
        if isinstance(market, dict):
            market["delta"] = round(float(market.get("delta", 0.0) or 0.0) + delta, 1)
            max_points = float(market.get("max", DIMENSION_WEIGHTS["market"]))
            market["score"] = round(max(0.0, min(max_points, max_points + market["delta"])), 1)
            flags = market.setdefault("flags", [])
            if flag not in flags:
                flags.append(flag)


def apply_news_scoring(r: ScanResult) -> None:
    """Apply news-driven bonus/penalty after ``compute_score``.

    ``compute_score`` runs before the news engines populate
    ``news_sentiment``/``news_card``, so this post-pass adjusts the safety
    score once the news data exists. The scanner calls it immediately after
    the news engines, so the final score/level/flags are all consistent
    before persistence and Telegram rendering.

    Rules:
      - High media attention (trending > 70) + BEARISH news → additional
        risk (-5, ``NEWS_BEARISH_HIGH_ATTENTION`` red flag).
      - High media attention + BULLISH news → reduced risk (+5,
        ``NEWS_BULLISH_HIGH_ATTENTION`` green flag).
      - Corroborated news (3+ distinct sources) with on-chain evidence →
        increased confidence (+5).

    Mutates ``r`` in place; no-op when there is no scanned news data.
    """
    ns = r.news_sentiment
    if not isinstance(ns, dict) or ns.get("result") != "scanned":
        return

    sentiment = str(ns.get("sentiment") or "NO_DATA").upper()
    trending = int(ns.get("trending_score", 0) or 0)
    if trending <= HIGH_MEDIA_ATTENTION_THRESHOLD:
        return

    if sentiment == "BEARISH":
        _apply_news_delta(r, -5.0, "NEWS_BEARISH_HIGH_ATTENTION", risk=True)
    elif sentiment == "BULLISH":
        _apply_news_delta(r, 5.0, "NEWS_BULLISH_HIGH_ATTENTION", risk=False)

    card = r.news_card
    if isinstance(card, dict):
        corr = card.get("corroboration") or {}
        total_sources = int(corr.get("total_sources", 0) or 0)
        if total_sources >= 3 and corr.get("on_chain_evidence"):
            r.confidence = min(100.0, r.confidence + 5)

    r.risk_level = _risk_level_from_score(r.safety_score)


# ── Market-aware risk adjustment (post-scoring enrichment) ────────────
# Market context is fetched BEFORE the scan phases, so the penalty/bonus is
# applied in a second pass (after compute_score) that keeps score/flags/
# breakdown in sync — the same pattern as apply_news_scoring.


def _apply_market_delta(r: ScanResult, delta: float, flag: str, *, risk: bool) -> None:
    """Apply a market delta to the safety score, flags, and market dimension."""
    r.safety_score = max(0.0, min(100.0, r.safety_score + delta))
    if risk:
        if flag not in r.risk_flags:
            r.risk_flags.append(flag)
    else:
        if flag not in r.green_flags:
            r.green_flags.append(flag)

    bd = r.breakdown
    if isinstance(bd, dict):
        market = bd.get("market")
        if isinstance(market, dict):
            market["delta"] = round(float(market.get("delta", 0.0) or 0.0) + delta, 1)
            max_points = float(market.get("max", DIMENSION_WEIGHTS["market"]))
            market["score"] = round(max(0.0, min(max_points, max_points + market["delta"])), 1)
            flags = market.setdefault("flags", [])
            if flag not in flags:
                flags.append(flag)


def apply_market_scoring(r: ScanResult) -> None:
    """Apply market-based risk adjustments after ``compute_score``.

    Uses ``r.market_context`` (fetched from RugBrain before the scan):

      - Fearful market (fear_index < 40) + new token (deployer freshness
        "new" or age < 48h) → higher risk (``MARKET_FEAR_NEW_TOKEN`` -5).
      - Recent exploits in the same category (recent_exploits > 0) →
        higher risk (``MARKET_RECENT_EXPLOITS`` -5, capped -10).
      - Rising chain volume (TVL change > +5%) is mildly constructive
        (``MARKET_CHAIN_GROWTH`` +3); falling volume (< -5%) is a mild
        risk (``MARKET_CHAIN_CONTRACTION`` -3).
      - High social velocity (>= 60) on a *new* token is a mild red flag
        (possible coordinated shill) (``MARKET_SOCIAL_PUMP`` -3).

    Mutates ``r`` in place; no-op when there is no market context.
    """
    mc = r.market_context
    if mc is None:
        return

    fear = float(mc.fear_index or 50)
    exploits = int(mc.recent_exploits or 0)
    chain_vol = float(mc.chain_volume_24h or 0)
    social = float(mc.social_velocity or 0)
    freshness = str(mc.deployer_freshness or "unknown")
    age_hours = float(getattr(r, "age_hours", 0) or 0)
    is_new = freshness == "new" or (0 < age_hours < 48)

    # Fearful market + new token → higher risk.
    if fear < 40 and is_new:
        _apply_market_delta(r, -5.0, "MARKET_FEAR_NEW_TOKEN", risk=True)

    # Recent exploits in the same category → higher risk (bounded).
    if exploits > 0:
        # TOP-100 #19: max() not min() — min() of two negatives always yields
        # the cap (-10) even for a single exploit; contract is -5 per exploit
        # capped at -10.
        penalty = max(-5.0 * min(exploits, 2), -10.0)
        _apply_market_delta(r, penalty, "MARKET_RECENT_EXPLOITS", risk=True)

    # Chain volume (TVL) direction.
    if chain_vol > 5:
        _apply_market_delta(r, 3.0, "MARKET_CHAIN_GROWTH", risk=False)
    elif chain_vol < -5:
        _apply_market_delta(r, -3.0, "MARKET_CHAIN_CONTRACTION", risk=True)

    # High social velocity on a new token — possible coordinated shill.
    if social >= 60 and is_new:
        _apply_market_delta(r, -3.0, "MARKET_SOCIAL_PUMP", risk=True)

    r.risk_level = _risk_level_from_score(r.safety_score)
