"""Retrieval scoring primitives — hits, context windows, query expansion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg.config import (
    RAG_EXPANSION_TERMS,
    RAG_HYBRID_ALPHA,
)

_QUERY_SYNONYMS: dict[str, list[str]] = {
    # ── scam / security ─────────────────────────────────────────────
    "rug": ["rugpull", "exit_scam", "liquidity_grab"],
    "scam": ["fraud", "scheme", "fake", "malicious"],
    "honeypot": ["cannot_sell", "tax_trap", "sell_restricted"],
    "phishing": ["fake_site", "lookalike", "spoof"],
    "drain": ["drainer", "sweeper", "token_stealer"],
    "pump": ["pump_and_dump", "artificial_volume", "wash_trade"],
    "whale": ["large_holder", "concentrated_ownership", "insider"],
    "exploit": ["vulnerability", "attack", "breach", "hack"],
    "sanction": ["ofac", "blacklist", "banned", "restricted"],
    "laundry": ["mixer", "tumbler", "coinjoin", "obfuscation"],
    "flash": ["flash_loan", "uncollateralized_loan", "arbitrage"],
    "reentrancy": ["reentrant", "recursive_call", "external_call"],
    "oracle": ["price_feed", "data_feed", "manipulation"],
    "approval": ["allowance", "approve", "spender", "revoke"],
    "mint": ["supply_creation", "inflation", "new_supply"],
    "frontrun": ["front_running", "tx_ordering", "mempool", "sandwich"],
    "upgrade": ["upgradeable", "proxy", "implementation_swap"],
    "tax": ["fee", "transfer_fee", "buy_sell_fee"],
    "lock": ["lockup", "locked_liquidity", "timelock"],
    # ── defi ────────────────────────────────────────────────────────
    "liquidity": ["liquidity_pool", "depth", "reserves", "lp"],
    "yield": ["apy", "rewards", "interest", "earnings"],
    "lp": ["liquidity_provider", "liquidity_pool", "reserves"],
    "mev": ["maximal_extractable_value", "sandwich", "front_running"],
    "arb": ["arbitrage", "price_difference", "spread_trade"],
    "arbitrage": ["price_difference", "spread_trade", "cross_venue"],
    "farm": ["yield_farming", "incentives", "reward_pool"],
    "lending": ["borrow", "collateral", "loan", "supply_rate"],
    "borrow": ["lending", "collateral", "debt", "health_factor"],
    "stablecoin": ["pegged", "peg", "collateralized", "algorithmic"],
    "tvl": ["total_value_locked", "deposits", "locked_assets"],
    "amm": ["automated_market_maker", "constant_product", "swap_pool"],
    "bridge": ["cross_chain", "wrapped_asset", "bridge_exploit"],
    "stake": ["staking", "delegate", "lock_tokens", "rewards"],
    "liquidat": ["liquidation", "cascade", "health_factor", "collateral_call"],
    "liquidation": ["cascade", "health_factor", "collateral_call", "margin_call"],
    "liquidations": ["liquidation", "cascade", "health_factor"],
    # ── ta ──────────────────────────────────────────────────────────
    "rsi": ["relative_strength_index", "momentum", "overbought", "oversold"],
    "macd": ["moving_average_convergence", "momentum", "histogram"],
    "support": ["demand_zone", "floor", "buy_wall", "liquidity_zone"],
    "resistance": ["supply_zone", "ceiling", "sell_wall", "liquidity_zone"],
    "divergence": ["momentum_conflict", "reversal_warning"],
    "momentum": ["rate_of_change", "velocity", "trend_strength"],
    "trend": ["uptrend", "downtrend", "structure", "higher_highs"],
    "breakout": ["consolidation", "range_break", "liquidity_sweep"],
    "volume": ["participation", "turnover", "wash_trade", "fake_volume"],
    "candle": ["candlestick", "wick", "engulfing", "rejection"],
    # ── tokenomics ──────────────────────────────────────────────────
    "vesting": ["cliff", "unlock_schedule", "lockup", "emission"],
    "emission": ["inflation", "mint_rate", "rewards_schedule", "supply_growth"],
    "supply": ["circulation", "float", "max_supply", "total_supply"],
    "burn": ["deflation", "token_removal", "buyback_burn"],
    "buyback": ["repurchase", "treasury", "burn"],
    "inflation": ["dilution", "emission", "supply_growth", "minting"],
    "allocation": ["distribution", "insider_bucket", "team_tokens"],
    "airdrop": ["claim", "sybil", "distribution_event"],
    "staking": ["lock", "rewards", "delegate", "ve_tokens"],
    # ── market microstructure ───────────────────────────────────────
    "slippage": ["price_impact", "spread", "execution_cost"],
    "spread": ["bid_ask", "market_maker", "execution_cost"],
    "depth": ["market_depth", "order_book", "liquidity"],
    "maker": ["market_maker", "limit_order", "liquidity_provider"],
    "taker": ["market_order", "crossing_spread", "liquidity_remover"],
    "leverage": ["margin", "liquidation", "notional", "open_interest"],
    "funding": ["funding_rate", "perpetual", "basis"],
    "oi": ["open_interest", "derivative_positions", "crowded_trade"],
    "order": ["order_book", "limit_order", "stop_order", "iceberg"],
    "orders": ["order_book", "limit_order", "stop_order"],
}

_QUERY_PHRASES: dict[str, str] = {
    "order_block": "supply demand zone institutional zone",
    "flash_loan": "uncollateralized loan instant arbitrage exploit",
    "impermanent_loss": "liquidity provider divergence holding",
    "order_book": "bid ask depth resting orders",
    "open_interest": "derivative positions leverage outstanding contracts",
    "funding_rate": "perpetual premium basis longs pay shorts",
    "rug_pull": "liquidity removal mint dump exit scam",
    "pump_dump": "artificial volume wash trade coordinated",
    "smart_contract": "program blockchain code audit vulnerability",
    "token_supply": "circulation float emission max supply",
}

_QUERY_VARIANTS: dict[str, list[str]] = {
    "defi": ["defi", "de-fi", "decentralized_finance"],
    "nft": ["nft", "non-fungible", "nonfungible"],
    "dex": ["dex", "decentralized_exchange", "amm"],
    "cex": ["cex", "centralized_exchange"],
}


log = get_logger(__name__)


@dataclass
class ScoredHit:
    """A search hit with additional relevance signals."""

    content: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    collection: str = ""
    doc_id: str = ""
    keyword_overlap: float = 0.0
    position_bonus: float = 0.0
    final_score: float = 0.0


@dataclass
class ContextWindow:
    """Assembled context ready for LLM consumption."""

    formatted: str = ""
    hits: list[ScoredHit] = field(default_factory=list)
    total_tokens_est: int = 0
    sources: list[str] = field(default_factory=list)
    took_ms: int = 0


class QueryExpander:
    """Expand a user query with synonyms and related terms for better recall.

    Strategy:
      1. Tokenise the query into lowercased terms.
      2. For each known term, append synonyms from the internal map.
      3. For each consecutive token pair, append phrase synonyms from the
         bigram map (e.g. ``order block`` → ``supply demand zone ...``).
      4. Return the original query plus an "expanded" version with the
         extra terms appended (space-separated, capped at ``max_terms``).

    Expansion is a recall lever only — the original query is always kept
    verbatim, so precision is never sacrificed.
    """

    def __init__(self) -> None:
        self.synonyms = _QUERY_SYNONYMS
        self.variants = _QUERY_VARIANTS
        self.phrases = _QUERY_PHRASES

    def expand(self, query: str, max_terms: int = RAG_EXPANSION_TERMS) -> str:
        """Return an expanded query with additional search terms."""
        if not query or not query.strip():
            return query

        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query.lower())
        extra_terms: list[str] = []
        # Bigram phrase expansion FIRST (more specific than single tokens):
        # consecutive token pairs (underscore-joined) hit the phrase map,
        # e.g. tokens "order","block" → "order_block" → "supply demand zone".
        for i in range(len(tokens) - 1):
            bigram = f"{tokens[i]}_{tokens[i + 1]}"
            if bigram in self.phrases:
                extra_terms.extend(self.phrases[bigram].split())
        # Then single-token synonyms and variants.
        for t in tokens:
            if t in self.synonyms:
                extra_terms.extend(self.synonyms[t])
            if t in self.variants:
                extra_terms.extend(v for v in self.variants[t] if v != t)

        # Deduplicate, limit, and append
        seen = set(tokens)
        extra = [et for et in extra_terms if et not in seen]
        expanded = query.strip()
        if extra:
            expanded += " " + " ".join(extra[:max_terms])

        log.debug("query_expanded", original_len=len(query), expanded_len=len(expanded))
        return expanded

    def expand_multi(self, queries: list[str]) -> list[str]:
        """Expand multiple queries in batch."""
        return [self.expand(q) for q in queries]


class RelevanceScorer:
    """Post-hoc relevance scoring with text-feature signals.

    Computes a composite score from:
      - Base similarity (from vector/keyword search)
      - Keyword overlap ratio between query and hit content
      - Position bonus (earlier results get a small boost)
    """

    def rescore(
        self,
        hits: list[dict[str, Any]],
        query: str,
        alpha: float = RAG_HYBRID_ALPHA,
    ) -> list[ScoredHit]:
        """Re-rank hits using additional relevance signals."""
        if not hits:
            return []

        query_terms = set(re.findall(r"\w+", query.lower()))
        scored: list[ScoredHit] = []

        for rank, h in enumerate(hits):
            content = h.get("content", h.get("text", ""))
            base_score = float(h.get("score", h.get("similarity", 0)))

            # Keyword overlap
            content_terms = set(re.findall(r"\w+", content.lower())) if content else set()
            overlap = len(query_terms & content_terms) / max(len(query_terms), 1) if query_terms else 0.0

            # Position bonus: earliest positions get +20% max, linearly decaying
            position_bonus = max(0.0, 1.0 - (rank / max(len(hits) * 2, 1)))

            # Composite: weighted base + keyword overlap + small position bonus
            final_score = alpha * base_score + (1.0 - alpha) * overlap * 0.8 + position_bonus * 0.05

            scored.append(
                ScoredHit(
                    content=content,
                    score=base_score,
                    metadata=h.get("metadata", {}),
                    collection=h.get("collection", ""),
                    doc_id=h.get("doc_id", h.get("id", "")),
                    keyword_overlap=overlap,
                    position_bonus=position_bonus,
                    final_score=final_score,
                )
            )

        # Sort by final score descending
        scored.sort(key=lambda s: -s.final_score)
        return scored

    def score_document(self, content: str, query: str) -> float:
        """Score a single document against a query. Returns 0-1."""
        if not content or not query:
            return 0.0
        query_terms = set(re.findall(r"\w+", query.lower()))
        content_terms = set(re.findall(r"\w+", content.lower()))
        overlap = len(query_terms & content_terms) / max(len(query_terms), 1)
        return min(1.0, overlap)
