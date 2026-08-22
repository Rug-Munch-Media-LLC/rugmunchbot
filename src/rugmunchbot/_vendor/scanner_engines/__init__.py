# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Scanner engines — data sources and detection modules.

Phase 1 (Complete):
  - own_price_oracle.py — CoinGecko replacement (TWAP/VWAP from dex_events)
  - own_dex_data.py — DexScreener replacement (DEX data from dex_events)

Phase 2 (Current):
  - own_security_analysis.py — GoPlus replacement (security from evm_transfers + deployer_events)
  - own_holder_analysis.py — Blockscout holder replacement (holders from evm_transfers)

Phase 4:
  - own_deployer_reputation.py — deployer scoring (deployer_events + dex_events)
  - own_whale_detect.py — whale wallets (dex_events + price oracle)
  - own_volume_authenticity.py — wash trading detection (dex_events + evm_transfers)
  - own_holder_age.py — holder vintage analysis (evm_transfers + deployer_events)

Phase 5 (RugMunchScan capabilities):
  - wallet_flow.py — C1 wallet-to-wallet flow visualization (evm_transfers + wallet_labels)
  - holder_clusters.py — C2 BubbleMaps-style holder clustering (evm_transfers + dex_events + wallet_labels)
  - new_token_radar.py — C3 new token discovery + early warning (deployer_events)
  - hype_score.py — C5 composite hype score (dex_events + evm_transfers + trending)
  - cross_chain_entity.py — C4 cross-chain entity resolution v1 (deployer_events + evm_transfers)
  - funding_analysis.py — clever wallet analysis: exchange-funded %, wallet freshness,
    holder overlap vs known scam tokens (evm_transfers + wallet_labels + deployer_history)
"""

# Public API exports
from rugmunchbot._vendor.scanner_engines.cross_chain_entity import resolve_cross_chain_entity
from rugmunchbot._vendor.scanner_engines.funding_analysis import (
    analyze_funding_sources,
    analyze_scam_overlap,
    compute_holder_overlap,
    compute_wallet_freshness,
)
from rugmunchbot._vendor.scanner_engines.holder_clusters import analyze_holder_clusters
from rugmunchbot._vendor.scanner_engines.news_sentiment import analyze_token_news_sentiment
from rugmunchbot._vendor.scanner_engines.own_cluster_analysis import get_own_cluster_analysis
from rugmunchbot._vendor.scanner_engines.own_cross_token_link import get_own_cross_token_link
from rugmunchbot._vendor.scanner_engines.own_decentralization import get_own_decentralization
from rugmunchbot._vendor.scanner_engines.own_deployer_reputation import get_own_deployer_reputation
from rugmunchbot._vendor.scanner_engines.own_deployer_timeline import get_own_deployer_timeline
from rugmunchbot._vendor.scanner_engines.own_dex_data import check_data_freshness, get_own_dex_data
from rugmunchbot._vendor.scanner_engines.own_holder_age import get_own_holder_age
from rugmunchbot._vendor.scanner_engines.own_holder_analysis import (
    get_holder_count,
    get_own_holder_analysis,
)
from rugmunchbot._vendor.scanner_engines.own_price_oracle import get_batch_prices, get_token_price
from rugmunchbot._vendor.scanner_engines.own_security_analysis import get_own_security
from rugmunchbot._vendor.scanner_engines.own_volume_authenticity import get_own_volume_authenticity
from rugmunchbot._vendor.scanner_engines.own_wallet_map import get_own_wallet_map
from rugmunchbot._vendor.scanner_engines.own_wallet_trust import get_wallet_trust
from rugmunchbot._vendor.scanner_engines.own_whale_detect import get_own_whale_detection

__all__ = [
    # Clever wallet analysis
    "analyze_funding_sources",
    # Phase 5 — RugMunchScan capabilities
    "analyze_holder_clusters",
    "analyze_scam_overlap",
    "analyze_token_news_sentiment",
    "check_data_freshness",
    "compute_holder_overlap",
    "compute_wallet_freshness",
    "get_batch_prices",
    "get_holder_count",
    # Phase 4
    "get_own_cluster_analysis",
    "get_own_cross_token_link",
    "get_own_decentralization",
    "get_own_deployer_reputation",
    "get_own_deployer_timeline",
    "get_own_dex_data",
    "get_own_holder_age",
    # Phase 2
    "get_own_holder_analysis",
    "get_own_security",
    "get_own_volume_authenticity",
    "get_own_wallet_map",
    "get_own_whale_detection",
    # Phase 1
    "get_token_price",
    "get_wallet_trust",
    "resolve_cross_chain_entity",
]
