#!/usr/bin/env python3
"""Backend endpoints, auth headers, health check, and external API URLs.

Resolution primitives here are UNCACHED (``_read_*``): the ``@lru_cache``
wrappers live in the package ``__init__`` so a package reload resets caches
exactly like the old single-module config did.
"""

from __future__ import annotations

import os

import httpx

# ══════════════════════════════════════════════
# API ENDPOINTS
# ══════════════════════════════════════════════
# Bot runs on HOST (not in Docker). Backend runs in Docker.
#   - Host → backend: http://localhost:8000
#   - Inside Docker → backend: http://rmi-backend:8000
# All three below are resolved lazily on first access (no env read / no
# filesystem probe at import).


def _read_in_docker() -> bool:
    """Docker detection primitive (env/fs probe per call; cached upstream)."""
    return bool(os.getenv("DOCKER_CONTAINER")) or os.path.exists("/.dockerenv")


def _read_backend_host() -> str:
    """Backend host primitive: ``rmi-backend`` in Docker, ``localhost`` otherwise."""
    return "rmi-backend:8000" if _read_in_docker() else "localhost:8000"


def _read_backend_url() -> str:
    """Backend base URL primitive (env ``RMI_BACKEND_URL`` override)."""
    return os.getenv("RMI_BACKEND_URL", f"http://{_read_backend_host()}")


def backend_headers() -> dict[str, str]:
    """Headers for bot→backend calls. X-API-Key authenticates AND bypasses CSRF.

    Late-imports the memoized ``_resolve_api_key`` wrapper so the key is
    resolved once per process (never shells out to gopass per call).
    """
    from rugmunchbot.config import _resolve_api_key

    key = _resolve_api_key()
    return {"X-API-Key": key} if key else {}


async def check_backend_health() -> None:
    """Ping backend /health on startup. Logs WARNING if unreachable."""
    from rugmunchbot.config import _resolve_backend_url

    backend_url = _resolve_backend_url()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{backend_url}/health", headers=backend_headers())
            if r.status_code == 200:
                from rugmunchbot.support.logging import get_logger

                get_logger("rmi.config").info("Backend reachable at %s", backend_url)
            else:
                from rugmunchbot.support.logging import get_logger

                get_logger("rmi.config").warning(
                    "Backend at %s returned status %d", backend_url, r.status_code
                )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        from rugmunchbot.support.logging import get_logger

        get_logger("rmi.config").warning("Backend UNREACHABLE at %s: %s", backend_url, exc)


# Alert engine
ALERT_CHECK_INTERVAL = 300  # seconds between alert checks (5 min)

# External APIs
DEXSCREENER = "https://api.dexscreener.com/latest/dex"
DEXSCREENER_TOKEN_BOOSTS = "https://api.dexscreener.com/token-boosts/top/v1"  # noqa: S105 — config default
SOLANAFM_API = "https://api.solana.fm/v0"
COINGECKO = "https://api.coingecko.com/api/v3"  # price history & trending
GOPLUS = "https://api.gopluslabs.com/api/v1"  # EVM token security scanner
HONEYPOT = "https://api.honeypot.is/v2"  # primary EVM security scanner


# ══════════════════════════════════════════════
# SENTINEL DEEP FORENSIC SCAN
# ══════════════════════════════════════════════
# Full 9-module SENTINEL deep scan (POST /api/v1/x402-tools/sentinel_scan).
# Returns composite risk score (0-100), per-module breakdown, red flags, and
# aggregated findings. See app/domains/scanners/sentinel_pipeline/config.py
# (SentinelReport) for the response schema.
def _read_sentinel_url() -> str:
    """Deepscan endpoint primitive (env override or derived from backend URL)."""
    return os.getenv("DEEPSCAN_URL", f"{_read_backend_url()}/api/v2/scanner/token/deepscan")


SENTINEL_TIMEOUT = 90.0  # deep forensic scan can take a while (all 20+ modules)
