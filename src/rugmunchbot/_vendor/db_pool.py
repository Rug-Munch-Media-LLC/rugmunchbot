# VENDORED 2026-08-24 from rmi-backend/app/core/db_pool.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Database connection pooling - Redis + Postgres with auto-reconnect."""

from __future__ import annotations

import os

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

try:
    from rugmunchbot._vendor.settings_shim import settings as _s

    REDIS_URL = _s.redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    PG_URL = _s.database_url or os.getenv("DATABASE_URL", "")
except Exception:
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    PG_URL = os.getenv("DATABASE_URL", "")

_redis_pool = None
_pg_pool = None
