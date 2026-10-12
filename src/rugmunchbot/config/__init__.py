#!/usr/bin/env python3
"""
CryptoRugMunch Bot - Configuration
==================================
Tiers, channels, API endpoints, DEX ref links, and constants.

Package layout (modularized from the former single-module config.py):
    _types.py       TypedDict definitions
    _secrets.py     gopass → env secret resolution (BOT_TOKEN / API_KEY / OWNER_IDS)
    _branding.py    branding & social constants
    _endpoints.py   backend URLs, auth headers, health check, external APIs
    _tiers.py       subscription tiers & purchase packs
    _reflinks.py    DEX referral links, Jupiter fee accounts, chain ids
    _channels.py    channel chat-id resolution
    _scheduling.py  env-driven channel scheduling switches/cadences
    _display.py     risk visualization & formatting helpers
    _education.py   Scam School topics

LAZY ACCESS CONTRACT (PEP 562): every secrets/env-driven name resolves on
first attribute access, never at import. Importing this package spawns no
gopass subprocess, reads no secret file, reads no env var, and probes no
filesystem. ``from ...config import X`` returns the identical snapshot as the
old eager binding — just on demand instead of at import.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from ._branding import (
    BOT_DESCRIPTION as BOT_DESCRIPTION,
    BOT_SHORT_DESCRIPTION as BOT_SHORT_DESCRIPTION,
    BOT_USERNAME as BOT_USERNAME,
    SUPPORT_EMAIL as SUPPORT_EMAIL,
    TELEGRAM_CHANNEL as TELEGRAM_CHANNEL,
    TELEGRAM_GROUP as TELEGRAM_GROUP,
    TWITTER_URL as TWITTER_URL,
    WEB_APP_URL as WEB_APP_URL,
    WEBSITE_URL as WEBSITE_URL,
)
from ._channels import (
    _CHANNEL_DEFAULTS as _CHANNEL_DEFAULTS,
    _CHANNEL_ENV_KEYS as _CHANNEL_ENV_KEYS,
    _CHANNEL_GOPASS_PATHS as _CHANNEL_GOPASS_PATHS,
    CHANNEL_ROLES as CHANNEL_ROLES,
    _channel_cache as _channel_cache,
    _channel_id_required as _channel_id_required,
    _resolve_channel_id as _resolve_channel_id,
    _resolve_channels as _resolve_channels,
    reset_channel_cache as reset_channel_cache,
)
from ._display import (
    RISK_COLORS as RISK_COLORS,
    _risk_gauge as _risk_gauge,
    fmt_chain as fmt_chain,
    fmt_number as fmt_number,
    fmt_pct as fmt_pct,
    risk_bar as risk_bar,
    threat_indicator as threat_indicator,
)
from ._education import SCAM_SCHOOL_TOPICS as SCAM_SCHOOL_TOPICS
from ._endpoints import (
    ALERT_CHECK_INTERVAL as ALERT_CHECK_INTERVAL,
    COINGECKO as COINGECKO,
    DEXSCREENER as DEXSCREENER,
    DEXSCREENER_TOKEN_BOOSTS as DEXSCREENER_TOKEN_BOOSTS,
    GOPLUS as GOPLUS,
    HONEYPOT as HONEYPOT,
    SENTINEL_TIMEOUT as SENTINEL_TIMEOUT,
    SOLANAFM_API as SOLANAFM_API,
    _read_backend_host as _read_backend_host,
    _read_backend_url as _read_backend_url,
    _read_in_docker as _read_in_docker,
    _read_sentinel_url as _read_sentinel_url,
    backend_headers as backend_headers,
    check_backend_health as check_backend_health,
)
from ._reflinks import (
    CHAIN_IDS as CHAIN_IDS,
    DEX_REF_LINKS as DEX_REF_LINKS,
    JUPITER_FEE_ACCOUNT as JUPITER_FEE_ACCOUNT,
    JUPITER_ULTRA_ACCOUNT as JUPITER_ULTRA_ACCOUNT,
)
from ._scheduling import (
    _env_bool as _env_bool,
    _env_float as _env_float,
    _env_int as _env_int,
    _read_alerts_channel_enabled as _read_alerts_channel_enabled,
    _read_alerts_channel_interval_minutes as _read_alerts_channel_interval_minutes,
    _read_alerts_channel_limit as _read_alerts_channel_limit,
    _read_alpha_channel_enabled as _read_alpha_channel_enabled,
    _read_alpha_channel_interval_hours as _read_alpha_channel_interval_hours,
    _read_alpha_channel_limit as _read_alpha_channel_limit,
    _read_channel_jobs_enabled as _read_channel_jobs_enabled,
    _read_news_channel_enabled as _read_news_channel_enabled,
    _read_news_channel_interval_hours as _read_news_channel_interval_hours,
    _read_news_channel_limit as _read_news_channel_limit,
    _read_scam_alerts_publisher_enabled,
    _read_scam_alerts_publisher_interval_minutes as _read_scam_alerts_publisher_interval_minutes,
    _read_scan_channel_enabled as _read_scan_channel_enabled,
    _read_scan_channel_interval_minutes as _read_scan_channel_interval_minutes,
    _read_scan_channel_limit as _read_scan_channel_limit,
    _read_scan_channel_tick_limit as _read_scan_channel_tick_limit,
    _read_updates_channel_enabled as _read_updates_channel_enabled,
    _read_updates_post_hour_utc,
)
from ._secrets import (
    _RMI_AUTH_SECRET_FILES as _RMI_AUTH_SECRET_FILES,
    _get_api_key as _get_api_key,
    _get_token as _get_token,
    _read_owner_ids as _read_owner_ids,
    _resolve_bot_token as _resolve_bot_token,
)
from ._tiers import (
    BUY_STARS_PACKS as BUY_STARS_PACKS,
    SCAN_PACKS as SCAN_PACKS,
    TIERS as TIERS,
    TOP_UP_PACKS as TOP_UP_PACKS,
    _read_beta_mode as _read_beta_mode,
)
from ._types import (
    DexRefLink as DexRefLink,
    ScanPack as ScanPack,
    StarsPack as StarsPack,
    TierDefinition as TierDefinition,
    TopUpPack as TopUpPack,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

# ══════════════════════════════════════════════
# CACHED RESOLVER WRAPPERS
# ══════════════════════════════════════════════
# The ``@lru_cache`` decorators intentionally live HERE (the package
# ``__init__``), not on the ``_read_*`` primitives in the submodules:
# ``importlib.reload()`` re-executes only this file, so a reload resets these
# caches exactly like the old single-module config.py did — while submodule
# code (which reload does NOT re-execute) stays cache-free.
#
# NOTE: inside this package's own functions use the ``_resolve_*`` helpers —
# module __getattr__ does NOT fire for function-scope globals.


@lru_cache
def _resolve_owner_ids() -> set[int]:
    """Lazy owner/operator ids — env read on first access, memoized per process."""
    return _read_owner_ids()


@lru_cache
def _resolve_api_key() -> str:
    """Memoized backend X-API-Key — resolves lazily on first use."""
    return _get_api_key()


@lru_cache
def _resolve_in_docker() -> bool:
    """Lazy Docker detection — first access only (no env/fs probe at import)."""
    return _read_in_docker()


@lru_cache
def _resolve_backend_host() -> str:
    """Lazy backend host: ``rmi-backend`` in Docker, ``localhost`` otherwise."""
    return _read_backend_host()


@lru_cache
def _resolve_backend_url() -> str:
    """Lazy backend base URL (env ``RMI_BACKEND_URL`` override)."""
    return _read_backend_url()


@lru_cache
def _resolve_sentinel_url() -> str:
    """Lazy deepscan endpoint (env override or derived from backend URL)."""
    return _read_sentinel_url()


@lru_cache
def _resolve_beta_mode() -> bool:
    """Lazy beta-mode flag (env ``RMI_BOT_BETA_MODE``) — read on first access."""
    return _read_beta_mode()


@lru_cache
def _resolve_channel_jobs_enabled() -> bool:
    return _read_channel_jobs_enabled()


@lru_cache
def _resolve_news_channel_enabled() -> bool:
    return _read_news_channel_enabled()


@lru_cache
def _resolve_alerts_channel_enabled() -> bool:
    return _read_alerts_channel_enabled()


@lru_cache
def _resolve_alpha_channel_enabled() -> bool:
    return _read_alpha_channel_enabled()


@lru_cache
def _resolve_news_channel_interval_hours() -> float:
    return _read_news_channel_interval_hours()


@lru_cache
def _resolve_alpha_channel_interval_hours() -> int:
    return _read_alpha_channel_interval_hours()


@lru_cache
def _resolve_alerts_channel_interval_minutes() -> int:
    return _read_alerts_channel_interval_minutes()


@lru_cache
def _resolve_news_channel_limit() -> int:
    return _read_news_channel_limit()


@lru_cache
def _resolve_alpha_channel_limit() -> int:
    return _read_alpha_channel_limit()


@lru_cache
def _resolve_alerts_channel_limit() -> int:
    return _read_alerts_channel_limit()


@lru_cache
def _resolve_scan_channel_enabled() -> bool:
    return _read_scan_channel_enabled()


@lru_cache
def _resolve_scan_channel_interval_minutes() -> int:
    return _read_scan_channel_interval_minutes()


@lru_cache
def _resolve_scan_channel_limit() -> int:
    return _read_scan_channel_limit()


@lru_cache
def _resolve_scan_channel_tick_limit() -> int:
    return _read_scan_channel_tick_limit()


@lru_cache
def _resolve_scam_alerts_publisher_enabled() -> bool:
    """Shill/scam alert publisher — on by default; no-ops without destinations."""
    return _read_scam_alerts_publisher_enabled()


def _resolve_scam_alerts_publisher_interval_minutes() -> int:
    return _read_scam_alerts_publisher_interval_minutes()


def _resolve_updates_channel_enabled() -> bool:
    return _read_updates_channel_enabled()


@lru_cache
def _resolve_updates_post_hour_utc() -> int:
    return _read_updates_post_hour_utc()


# ══════════════════════════════════════════════
# LAZY ACCESS (PEP 562 module __getattr__)
# ══════════════════════════════════════════════
# EVERY secrets/env-driven name resolves on first attribute access, never at
# import: importing this package spawns no gopass subprocess, reads no secret
# file (/srv/secrets/rmi_auth_token), reads no env var, and probes no
# filesystem (/srv/secrets, /.dockerenv). The first access runs the
# ``@lru_cache`` resolver and the value is memoized into the package
# namespace, so ``from ...config import X`` returns the identical snapshot as
# the old eager binding — just on demand instead of at import.

# Type-only declarations for the lazy names — annotations do NOT create runtime
# bindings (that is the point: no side effects at import), but they keep static
# analyzers (ruff F821 / mypy) aware of the public names resolved by module
# __getattr__ below. NOTE: inside this package's own functions use the
# ``_resolve_*`` helpers — module __getattr__ does NOT fire for function-scope
# globals.
BOT_TOKEN: str
API_KEY: str
CHANNELS: dict[str, str]
OWNER_IDS: set[int]
_IN_DOCKER: bool
BACKEND_HOST: str
BACKEND_URL: str
SENTINEL_URL: str
BETA_MODE: bool
CHANNEL_JOBS_ENABLED: bool
NEWS_CHANNEL_ENABLED: bool
ALERTS_CHANNEL_ENABLED: bool
ALPHA_CHANNEL_ENABLED: bool
NEWS_CHANNEL_INTERVAL_HOURS: float
ALPHA_CHANNEL_INTERVAL_HOURS: int
ALERTS_CHANNEL_INTERVAL_MINUTES: int
NEWS_CHANNEL_LIMIT: int
ALPHA_CHANNEL_LIMIT: int
ALERTS_CHANNEL_LIMIT: int
SCAN_CHANNEL_ENABLED: bool
SCAN_CHANNEL_INTERVAL_MINUTES: int
SCAN_CHANNEL_LIMIT: int
SCAN_CHANNEL_TICK_LIMIT: int
UPDATES_CHANNEL_ENABLED: bool
UPDATES_POST_HOUR_UTC: int
SCAM_ALERTS_PUBLISHER_ENABLED: bool
SCAM_ALERTS_PUBLISHER_INTERVAL_MINUTES: int

_LAZY_RESOLVERS: dict[str, Callable[[], Any]] = {
    "BOT_TOKEN": _resolve_bot_token,
    "API_KEY": _resolve_api_key,
    "CHANNELS": _resolve_channels,
    "OWNER_IDS": _resolve_owner_ids,
    "_IN_DOCKER": _resolve_in_docker,
    "BACKEND_HOST": _resolve_backend_host,
    "BACKEND_URL": _resolve_backend_url,
    "SENTINEL_URL": _resolve_sentinel_url,
    "BETA_MODE": _resolve_beta_mode,
    "CHANNEL_JOBS_ENABLED": _resolve_channel_jobs_enabled,
    "NEWS_CHANNEL_ENABLED": _resolve_news_channel_enabled,
    "ALERTS_CHANNEL_ENABLED": _resolve_alerts_channel_enabled,
    "ALPHA_CHANNEL_ENABLED": _resolve_alpha_channel_enabled,
    "NEWS_CHANNEL_INTERVAL_HOURS": _resolve_news_channel_interval_hours,
    "ALPHA_CHANNEL_INTERVAL_HOURS": _resolve_alpha_channel_interval_hours,
    "ALERTS_CHANNEL_INTERVAL_MINUTES": _resolve_alerts_channel_interval_minutes,
    "NEWS_CHANNEL_LIMIT": _resolve_news_channel_limit,
    "ALPHA_CHANNEL_LIMIT": _resolve_alpha_channel_limit,
    "ALERTS_CHANNEL_LIMIT": _resolve_alerts_channel_limit,
    "SCAN_CHANNEL_ENABLED": _resolve_scan_channel_enabled,
    "SCAN_CHANNEL_INTERVAL_MINUTES": _resolve_scan_channel_interval_minutes,
    "SCAN_CHANNEL_LIMIT": _resolve_scan_channel_limit,
    "SCAN_CHANNEL_TICK_LIMIT": _resolve_scan_channel_tick_limit,
    "UPDATES_CHANNEL_ENABLED": _resolve_updates_channel_enabled,
    "UPDATES_POST_HOUR_UTC": _resolve_updates_post_hour_utc,
    "SCAM_ALERTS_PUBLISHER_ENABLED": _resolve_scam_alerts_publisher_enabled,
    "SCAM_ALERTS_PUBLISHER_INTERVAL_MINUTES": _resolve_scam_alerts_publisher_interval_minutes,
}


def __getattr__(name: str) -> Any:
    """Resolve lazy config names on first access (PEP 562), then memoize."""
    resolver = _LAZY_RESOLVERS.get(name)
    if resolver is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = resolver()
    globals()[name] = value
    return value
