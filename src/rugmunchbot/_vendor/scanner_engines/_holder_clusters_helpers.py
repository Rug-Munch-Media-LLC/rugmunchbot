# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/_holder_clusters_helpers.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Pure holder-clustering helpers (extracted from ``engines/holder_clusters.py``).

Degodding split (2026-08-13): the three cluster detection passes
(shared-funder / same-block / bundle), holder labeling
(``_classify_holder``), concentration classification, and the Gini / HHI
metric computations are pure logic with no ClickHouse or Redis access.
They live here so ``holder_clusters.py`` stays focused on the query layer
+ orchestration (tests patch ``holder_clusters._get_ch_client`` end-to-end,
so the queries remain in the engine module).

Every name is re-exported by ``holder_clusters.py`` so
``hc._build_funder_clusters`` / ``hc._classify_holder`` etc. keep resolving
in tests (test_holder_clusters uses them directly).
"""

from __future__ import annotations

from typing import Any

# ── Cluster thresholds ────────────────────────────────────────────────────
_FUNDER_CLUSTER_MIN = 3  # groups >2 holders from one funder are flagged
_SAME_BLOCK_CLUSTER_MIN = 3  # >=3 simultaneous first buys flag a block
_BUNDLE_CLUSTER_MIN = 2  # bundle block must map to >=2 holders

# ── Classification thresholds ─────────────────────────────────────────────
_SNIPER_WINDOW_SECONDS = 60  # first-seen within 60s of deploy => sniper
_SNIPER_WINDOW_BLOCKS = 2  # ... or within 2 blocks of deploy block
_WHALE_PCT_THRESHOLD = 5.0  # holder with >=5% tracked supply => whale

_ZERO_ADDR = "0x0000000000000000000000000000000000000000"
_DEAD_ADDR = "0x000000000000000000000000000000000000dead"

# ── Label classification keywords (match category/subtype/name, lowercased)
_CEX_KEYWORDS = (
    "cex",
    "exchange",
    "binance",
    "coinbase",
    "kraken",
    "okx",
    "okex",
    "bybit",
    "huobi",
    "kucoin",
    "gate.io",
    "gateio",
    "mexc",
    "bitget",
    "upbit",
    "bithumb",
    "gemini",
    "bitstamp",
    "crypto.com",
)
_DEX_KEYWORDS = (
    "dex",
    "router",
    "amm",
    "aggregator",
    "uniswap",
    "pancake",
    "sushiswap",
    "curve",
    "balancer",
    "1inch",
    "camelot",
    "trader joe",
    "spooky",
    "quickswap",
)
_BRIDGE_KEYWORDS = (
    "bridge",
    "wormhole",
    "axelar",
    "layerzero",
    "multichain",
    "hop protocol",
)
_MIXER_KEYWORDS = (
    "mixer",
    "tornado",
    "blender",
    "coinjoin",
    "wasabi",
    "privacy",
)
_TEAM_KEYWORDS = (
    "team",
    "creator",
    "founder",
    "deployer",
    "multisig",
    "treasury",
    "dev wallet",
)
_BOT_KEYWORDS = (
    "bot",
    "mev",
    "arb bot",  # "arb" alone substring-matches "arbitrum" labels
    "automated",
    "flash",
)

_CONCENTRATION_DESCRIPTIONS = {
    "LOW": "Distributed — top 10 holders control under 30% of tracked supply.",
    "MEDIUM": "Moderately concentrated — top 10 holders control 30-50%.",
    "HIGH": "Concentrated — top 10 holders control 50-80%.",
    "EXTREME": "Extremely concentrated — top 10 holders control over 80%.",
}


# ═══════════════════════════════════════════════════════════════════════════
# CLUSTER DETECTION PASSES (pure logic — unit-testable)
# ═══════════════════════════════════════════════════════════════════════════


def _build_funder_clusters(
    first_in: dict[str, dict[str, Any]],
    deployer: str | None,
    pool_addresses: set[str],
    token_address: str,
) -> list[dict[str, Any]]:
    """Pass 1 — group holders by shared first in-transfer funder (groups >2)."""
    funder_groups: dict[str, list[str]] = {}
    for holder, info in first_in.items():
        funder = info.get("funder")
        if not funder or funder in (_ZERO_ADDR, _DEAD_ADDR):
            continue
        if funder == token_address or funder in pool_addresses:
            # Pool / token-contract sources cluster every buyer — not meaningful.
            continue
        funder_groups.setdefault(funder, []).append(holder)

    clusters: list[dict[str, Any]] = []
    for idx, (funder, addresses) in enumerate(funder_groups.items()):
        if len(addresses) < _FUNDER_CLUSTER_MIN:
            continue
        is_deployer = bool(deployer) and funder == deployer
        confidence = min(0.95, 0.55 + 0.1 * (len(addresses) - _FUNDER_CLUSTER_MIN) + (0.05 if is_deployer else 0.0))
        blocks: list[int] = []
        tx_hashes: list[str] = []
        for a in addresses:
            first_block = first_in[a].get("first_block")
            if first_block is not None:
                blocks.append(int(first_block))
            first_tx = first_in[a].get("first_tx")
            if first_tx:
                tx_hashes.append(str(first_tx))
        clusters.append(
            {
                "id": f"funder:{idx}",
                "addresses": addresses,
                "funder": funder,
                "first_block": min(blocks) if blocks else None,
                "reason": "deployer_funded" if is_deployer else "shared_funder",
                "confidence": round(confidence, 2),
                "combined_pct": 0.0,
                "evidence": {
                    "funder": funder,
                    "funder_is_deployer": is_deployer,
                    "first_tx_hashes": tx_hashes[:5],
                },
            }
        )
    return clusters


def _build_same_block_clusters(first_buy: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Pass 2 — holders whose first DEX buy landed in the same block."""
    block_groups: dict[int, list[str]] = {}
    for buyer, info in first_buy.items():
        block = info.get("first_block")
        if block is not None:
            block_groups.setdefault(block, []).append(buyer)

    clusters: list[dict[str, Any]] = []
    for idx, (block, addresses) in enumerate(sorted(block_groups.items())):
        if len(addresses) < _SAME_BLOCK_CLUSTER_MIN:
            continue
        confidence = min(0.9, 0.45 + 0.08 * (len(addresses) - _SAME_BLOCK_CLUSTER_MIN))
        tx_hashes = [first_buy[a].get("first_tx") for a in addresses if first_buy[a].get("first_tx")]
        clusters.append(
            {
                "id": f"block:{idx}",
                "addresses": addresses,
                "funder": None,
                "first_block": block,
                "reason": "same_block_buy",
                "confidence": round(confidence, 2),
                "combined_pct": 0.0,
                "evidence": {
                    "block_number": block,
                    "buyers": len(addresses),
                    "first_tx_hashes": tx_hashes[:5],
                },
            }
        )
    return clusters


def _build_bundle_clusters(
    bundle_result: dict[str, Any] | None,
    first_in: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pass 3 — surface bundle-buy groups from own_bundle_detect output."""
    if not bundle_result:
        return []
    bundle_conf = 0.4
    try:
        bundle_conf = max(0.4, float(bundle_result.get("confidence") or 0) / 100.0)
    except (TypeError, ValueError):
        bundle_conf = 0.4

    clusters: list[dict[str, Any]] = []
    for bb in bundle_result.get("bundle_blocks", []) or []:
        block = bb.get("block_number")
        if block is None:
            continue
        addresses = [h for h, info in first_in.items() if info.get("first_block") == block]
        if len(addresses) < _BUNDLE_CLUSTER_MIN:
            continue
        clusters.append(
            {
                "id": f"bundle:{block}",
                "addresses": addresses,
                "funder": None,
                "first_block": block,
                "reason": "bundle",
                "confidence": round(min(0.95, bundle_conf), 2),
                "combined_pct": 0.0,
                "evidence": {
                    "block_number": block,
                    "buyer_count": bb.get("buyer_count", len(addresses)),
                    "total_buys": bb.get("total_buys", 0),
                },
            }
        )
    return clusters


# ═══════════════════════════════════════════════════════════════════════════
# HOLDER LABELING (pure logic — unit-testable)
# ═══════════════════════════════════════════════════════════════════════════


def _classify_holder(
    holder: dict[str, Any],
    deploy: dict[str, Any] | None,
) -> str:
    """Classify a holder into CEX/DEX/bridge/mixer/team/deployer/sniper/bot/whale/normal.

    Priority:
      1. deployer — the token deployer itself
      2. institutional labels — CEX / DEX / bridge / mixer (never snipers)
      3. sniper — first-seen within the deploy window (first-block buy pattern)
      4. team / bot label keywords
      5. deployer-linked funding => team
      6. whale — >=5% of tracked supply
      7. normal — fallback
    """
    address = str(holder.get("address") or "").lower()
    deployer = (deploy or {}).get("deployer")
    if deployer and address == str(deployer).lower():
        return "deployer"

    labels: list[dict[str, Any]] = holder.get("labels") or []
    text_parts = []
    for lbl in labels:
        text_parts.append(" ".join(str(lbl.get(k) or "") for k in ("label_category", "label_subtype", "label_name")))
    text = " ".join(text_parts).lower()

    if any(kw in text for kw in _CEX_KEYWORDS):
        return "CEX"
    if any(kw in text for kw in _DEX_KEYWORDS):
        return "DEX"
    if any(kw in text for kw in _BRIDGE_KEYWORDS):
        return "bridge"
    if any(kw in text for kw in _MIXER_KEYWORDS):
        return "mixer"

    first_seen = holder.get("first_seen")
    deploy_ts = (deploy or {}).get("deploy_ts")
    deploy_block = (deploy or {}).get("deploy_block")
    if first_seen is not None and deploy_ts is not None:
        diff = first_seen - deploy_ts
        if 0 <= diff <= _SNIPER_WINDOW_SECONDS:
            return "sniper"
    first_block = holder.get("first_block")
    if (
        first_block is not None
        and deploy_block is not None
        and 0 <= (first_block - deploy_block) <= _SNIPER_WINDOW_BLOCKS
    ):
        return "sniper"

    if any(kw in text for kw in _TEAM_KEYWORDS):
        return "team"
    if any(kw in text for kw in _BOT_KEYWORDS):
        return "bot"

    funder = holder.get("funder")
    if deployer and funder and str(funder).lower() == str(deployer).lower():
        return "team"

    if float(holder.get("balance_pct") or 0) >= _WHALE_PCT_THRESHOLD:
        return "whale"

    return "normal"


def _classify_concentration(top10_pct: float) -> str:
    """Map top-10 holder share to LOW / MEDIUM / HIGH / EXTREME."""
    if top10_pct >= 80:
        return "EXTREME"
    if top10_pct >= 50:
        return "HIGH"
    if top10_pct >= 30:
        return "MEDIUM"
    return "LOW"


def _compute_hhi(balances: list[float]) -> float:
    """Herfindahl-Hirschman Index — 0 (distributed) to 10,000 (single holder)."""
    total = sum(balances)
    if total <= 0:
        return 10000.0
    return round(sum((b / total) ** 2 for b in balances) * 10000, 2)


def _compute_gini(balances: list[float]) -> float:
    """Gini coefficient (0 = equality, 1 = perfect inequality)."""
    if len(balances) < 2:
        return 0.0
    total = sum(balances)
    if total <= 0:
        return 0.0
    sorted_b = sorted(balances)
    n = len(sorted_b)
    weighted = sum((i + 1) * b for i, b in enumerate(sorted_b))
    gini = (2 * weighted) / (n * total) - (n + 1) / n
    return max(0.0, min(1.0, gini))
