# VENDORED 2026-08-23 from rmi-backend/app/databus/webshare_proxies.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Load the configured outbound proxy list.

The proxy list is supplied by the deployment via environment variables and
consumed by the proxy fetch layer. When no proxy configuration is present the
loader returns ``[]`` so callers degrade gracefully.

Configuration (env vars):

    PROXY_LIST  space/newline/comma separated proxy URLs
    PROXY_URL   a single proxy URL

Credentials are never hardcoded and never logged. Log lines carry only the
source and the count of proxies; the URLs themselves (which embed
``user:pass@``) are never written to the log.
"""

from __future__ import annotations

import os
from typing import Final

from rugmunchbot._vendor.logging_shim import get_logger

log = get_logger(__name__)

_PROXY_URL_SCHEMES: Final[tuple[str, ...]] = ("http://", "https://", "socks5://", "socks5h://")


def parse_proxy_list(raw: str) -> list[str]:
    """Parse a space / newline / comma separated list of proxy URLs."""
    entries: list[str] = []
    for chunk in raw.replace(",", " ").split():
        chunk = chunk.strip()
        if chunk.startswith(_PROXY_URL_SCHEMES):
            entries.append(chunk)
    return entries


def load_proxies() -> list[str]:
    """Return the configured outbound proxy list.

    Priority: ``PROXY_LIST`` env → ``PROXY_URL`` env. Returns ``[]`` when no
    source is available so the proxy tier degrades gracefully.
    """
    listed = os.getenv("PROXY_LIST", "").strip()
    if listed:
        parsed = parse_proxy_list(listed)
        if parsed:
            log.info("proxies_loaded", source="env_list", count=len(parsed))
            return parsed
    single = os.getenv("PROXY_URL", "").strip()
    if single and single.startswith(_PROXY_URL_SCHEMES):
        log.info("proxies_loaded", source="env_url", count=1)
        return [single]
    return []


def clear_proxy_cache() -> None:
    """No-op retained for API compatibility; proxy config is read fresh each call."""
    return None
