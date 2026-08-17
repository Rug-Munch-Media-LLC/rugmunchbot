# VENDORED 2026-08-17 from rmi-backend/app/databus/rugcharts_intel/cache.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
RugCharts Intel Cache Layer — Redis helpers and configuration.
"""

from __future__ import annotations

import os
from typing import Any

import redis

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger("rugcharts_intel")

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")
ARKHAM_KEY = os.getenv("ARKHAM_API_KEY", "")
MORALIS_KEY = os.getenv("MORALIS_API_KEY", "")

CACHE_TTL = {
    "smart_money": 300,
    "whale_alert": 60,
    "token_launch": 120,
    "insider": 600,
    "liquidity": 300,
    "holder_health": 600,
    "cross_chain": 3600,
    "rug_pattern": 7200,
    "dev_reputation": 86400,
    "token_report": 300,
}


def _r() -> Any:
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=2,
    )
