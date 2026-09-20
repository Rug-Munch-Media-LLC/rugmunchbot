# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/__init__.py (frozen copy; do not edit).
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
# Only the modules that survived the 2026-09-20 cutover are re-exported here.
# The rest of the upstream engine set was removed; import those submodules
# directly (e.g. ``...scanner_engines.rpc_router``) where still needed.
from rugmunchbot._vendor.scanner_engines.own_wallet_map import get_own_wallet_map
from rugmunchbot._vendor.scanner_engines.own_wallet_trust import get_wallet_trust

__all__ = [
    "get_own_wallet_map",
    "get_wallet_trust",
]
