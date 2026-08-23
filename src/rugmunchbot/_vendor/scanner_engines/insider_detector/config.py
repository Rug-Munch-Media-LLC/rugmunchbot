# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/insider_detector/config.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Constants and configuration for insider detection."""

# DexScreener API endpoints
from __future__ import annotations

DEXSCREENER_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_BOOSTS = "https://api.dexscreener.com/token-boosts/top/v1"
DEXSCREENER_TOKEN = "https://api.dexscreener.com/latest/dex/tokens/{}"  # noqa: S105 — config default
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search?q={}"

# Estimated multipliers for PnL calculation
AVG_PRE_TRENDING_PUMP = 8.0
AVG_CEX_LISTING_PUMP = 3.5
AVG_KOL_PUMP = 5.0
AVG_PUMPFUN_GRADUATION_PUMP = 6.0

# Note: CEX_LISTING_MULTIPLIER is an alias used in the code
CEX_LISTING_MULTIPLIER = 3.5

# Lead time windows for classification (minutes)
INSIDER_WINDOW_MIN = 15  # Less than this = sniper, not insider
PRE_TRENDING_WINDOW_MAX = 1440  # 24h max window for pre-trending
PRE_KOL_WINDOW_MAX = 720  # 12h max window for pre-KOL
PRE_CEX_WINDOW_MAX = 4320  # 72h max window for pre-CEX listing
PRE_GRADUATION_WINDOW_MAX = 720  # 12h max window for pre-graduation

# Minimum buy amount to be considered an insider (USD)
MIN_INSIDER_BUY_USD = 100
