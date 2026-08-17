# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/insider_detector/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
Insider Detection Engine — Split into focused modules.
Re-exports all public API for backward compatibility.
"""

from rugmunchbot._vendor.logging_shim import get_logger

from .collectors import (
    _collect_token_meta,
    _gather_events,
    _get_token_traders,
)
from .config import (
    AVG_CEX_LISTING_PUMP,
    AVG_KOL_PUMP,
    AVG_PRE_TRENDING_PUMP,
    AVG_PUMPFUN_GRADUATION_PUMP,
    DEXSCREENER_BOOSTS,
    DEXSCREENER_PROFILES,
    DEXSCREENER_SEARCH,
    DEXSCREENER_TOKEN,
    INSIDER_WINDOW_MIN,
    MIN_INSIDER_BUY_USD,
)
from .detectors import (
    _detect_cex_frontrun,
    _detect_dev_insider,
    _detect_pre_kol,
    _detect_pre_trending,
    _detect_pumpfun_graduation,
    _detect_snipers,
)
from .helpers import (
    _parse_timestamp,
    _ts_human,
)
from .models import Confidence, InsiderEvidence, InsiderReport, InsiderType
from .scoring import detect_insiders, find_pre_pump_wallets, get_insider_score
from .utils import (
    _check_early_buy,
    _deduplicate_evidence,
    _generate_insights,
)

logger = get_logger(__name__)

logger.info("Insider Detection Engine loaded: %d detection methods, confidence scoring, PnL estimation", 6)

__all__ = [
    "AVG_CEX_LISTING_PUMP",
    "AVG_KOL_PUMP",
    "AVG_PRE_TRENDING_PUMP",
    "AVG_PUMPFUN_GRADUATION_PUMP",
    "DEXSCREENER_BOOSTS",
    "DEXSCREENER_PROFILES",
    "DEXSCREENER_SEARCH",
    "DEXSCREENER_TOKEN",
    "INSIDER_WINDOW_MIN",
    "MIN_INSIDER_BUY_USD",
    "Confidence",
    "InsiderEvidence",
    "InsiderReport",
    "InsiderType",
    "_check_early_buy",
    "_collect_token_meta",
    "_deduplicate_evidence",
    "_detect_cex_frontrun",
    "_detect_dev_insider",
    "_detect_pre_kol",
    "_detect_pre_trending",
    "_detect_pumpfun_graduation",
    "_detect_snipers",
    "_gather_events",
    "_generate_insights",
    "_get_token_traders",
    "_parse_timestamp",
    "_ts_human",
    "detect_insiders",
    "find_pre_pump_wallets",
    "get_insider_score",
]
