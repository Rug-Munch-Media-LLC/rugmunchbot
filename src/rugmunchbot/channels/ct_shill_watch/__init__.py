"""CT timeline shill watch — wall of shame / confirmation / daily scoring.

Facade package (split from ``channels/ct_shill_watch.py``, 2026-10-25): the
complete pre-split public surface stays importable from this module. X
ingestion (fetch, normalize, embed, ingest) is owned by **rugbrain**; the
X-fetch tick and retweeter discovery are fail-open no-ops. Fail-open
everywhere: dead scanner, dead Qdrant, read-only data dir → zero catches,
never raises.
"""

from __future__ import annotations

from ._config import (
    CT_SHILL_ACCOUNTS_PER_TICK as CT_SHILL_ACCOUNTS_PER_TICK,
)
from ._config import (
    CT_SHILL_ENABLED as CT_SHILL_ENABLED,
)
from ._config import (
    CT_SHILL_INTERVAL_MIN as CT_SHILL_INTERVAL_MIN,
)
from ._config import (
    CT_SHILL_MAX_ADDS_PER_TICK as CT_SHILL_MAX_ADDS_PER_TICK,
)
from ._config import (
    CT_SHILL_MIN_ENGAGEMENT as CT_SHILL_MIN_ENGAGEMENT,
)
from ._config import (
    CT_SHILL_SCREENSHOT_BUDGET as CT_SHILL_SCREENSHOT_BUDGET,
)
from ._config import (
    CT_SHILL_TWEETS_PER_ACCOUNT as CT_SHILL_TWEETS_PER_ACCOUNT,
)
from ._config import (
    INFLUENTIAL_ACCOUNTS_PATH as INFLUENTIAL_ACCOUNTS_PATH,
)
from ._config import (
    KOL_DAILY_INTERVAL_MIN as KOL_DAILY_INTERVAL_MIN,
)
from ._config import (
    KOL_DAILY_MAX_RETWEETER_PULLS as KOL_DAILY_MAX_RETWEETER_PULLS,
)
from ._config import (
    KOL_DAILY_REFRESH_ENABLED as KOL_DAILY_REFRESH_ENABLED,
)
from ._config import (
    SOCIAL_INTEL_COLLECTION as SOCIAL_INTEL_COLLECTION,
)
from ._config import (
    WALL_OF_SHAME_PATH as WALL_OF_SHAME_PATH,
)
from ._confirm import confirm_shill, tg_cross_ref
from ._embed import embed_shill
from ._registry import load_ct_handles, next_account_batch
from ._runner import discover_from_retweeters, run_ct_shill_watch, run_kol_daily_refresh
from ._score import score_kol
from ._wall import record_shill, rescore_wall_of_shame

__all__ = [
    "CT_SHILL_ENABLED",
    "CT_SHILL_INTERVAL_MIN",
    "KOL_DAILY_INTERVAL_MIN",
    "SOCIAL_INTEL_COLLECTION",
    "WALL_OF_SHAME_PATH",
    "confirm_shill",
    "discover_from_retweeters",
    "embed_shill",
    "load_ct_handles",
    "next_account_batch",
    "record_shill",
    "rescore_wall_of_shame",
    "run_ct_shill_watch",
    "run_kol_daily_refresh",
    "score_kol",
    "tg_cross_ref",
]
