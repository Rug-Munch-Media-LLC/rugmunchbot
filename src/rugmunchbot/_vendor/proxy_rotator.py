# VENDORED 2026-08-23 from rmi-backend/app/databus/proxy_rotator.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Residential proxy rotation + fallback for Cloudflare-blocked data providers.

GMGN, DexScreener and friends return 403 (Cloudflare) from datacenter IPs.
This module routes their httpx traffic through the Webshare residential
proxy fleet and rotates proxies with automatic fallback.

Configuration (env vars, read once per process by the singleton):

    WEBSHARE_PROXY_LIST   comma/space/newline separated proxy entries. Each
                          entry is ``ip:port`` or ``user:pass@ip:port``.
    WEBSHARE_PROXY_USER   shared username applied to bare ``ip:port`` entries
    WEBSHARE_PROXY_PASS   shared password applied to bare ``ip:port`` entries

When ``WEBSHARE_PROXY_LIST`` is empty the rotator is disabled (``enabled`` is
False) and every client it builds is direct — the proxy tier degrades
gracefully instead of failing.

Recommended fallback pattern (proxy -> next proxy -> direct)
------------------------------------------------------------

    rotator = get_proxy_rotator()
    proxy = rotator.get_rotating_proxy()      # proxy A (None -> direct)
    client = rotator.get_client(proxy=proxy)
    try:
        resp = await client.get(url)
    except httpx.TransportError:
        rotator.mark_failed(proxy)            # A on cooldown
        proxy = rotator.next_after_failure()  # proxy B, or None when all down
        if proxy is not None:
            resp = await rotator.get_client(proxy=proxy).get(url)
    if resp.status_code in (403, 429):        # Cloudflare block / rate limit
        rotator.mark_failed(proxy)
        proxy = rotator.next_after_failure()
        if proxy is not None:                 # retry once on the next proxy
            resp = await rotator.get_client(proxy=proxy).get(url)
    if resp.status_code in (403, 429):        # exhausted -> go direct
        resp = await rotator.get_client(proxy=None).get(url)

Failed proxies stay on cooldown (``FAILURE_COOLDOWN`` seconds) and are
skipped by rotation until they cool down. Credentials are never logged —
log lines carry only the host and the failure count.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Final, cast
from urllib.parse import quote, urlsplit

import httpx

from rugmunchbot._vendor.logging_shim import get_logger

log = get_logger(__name__)

# ── Configuration ────────────────────────────────────────────────────

PROXY_LIST_ENV: Final[str] = "WEBSHARE_PROXY_LIST"
PROXY_USER_ENV: Final[str] = "WEBSHARE_PROXY_USER"
PROXY_PASS_ENV: Final[str] = "WEBSHARE_PROXY_PASS"  # noqa: S105 — env var name, not a credential

DEFAULT_TIMEOUT: Final[float] = 30.0
DEFAULT_CONNECT_TIMEOUT: Final[float] = 10.0
FAILURE_COOLDOWN: Final[float] = 60.0  # seconds a failed proxy stays disabled

_URL_SCHEMES: Final[tuple[str, ...]] = ("http://", "https://", "socks5://", "socks5h://")

# Sentinel distinguishing "no proxy arg passed" (auto-pick the next rotating
# proxy) from an explicit ``proxy=None`` (force direct). A plain None default
# cannot express both.
_AUTO_PROXY: Final = object()


def build_proxy_url(entry: str, username: str = "", password: str = "") -> str | None:
    """Normalize one proxy entry into a full ``scheme://[user:pass@]host:port`` URL.

    ``entry`` may be ``ip:port`` or ``user:pass@ip:port`` and may carry an
    explicit scheme (``http://`` is the default). Embedded credentials win
    over the shared ``username``/``password``. Returns None for invalid
    entries (missing host, non-numeric port). Credentials are URL-encoded.
    """
    value = entry.strip()
    if not value:
        return None
    scheme = "http"
    for prefix in _URL_SCHEMES:
        if value.startswith(prefix):
            scheme = prefix[:-3]  # strip "://" -> "http" | "https" | "socks5" | "socks5h"
            value = value[len(prefix) :]
            break
    embedded_user = ""
    embedded_pass = ""
    if "@" in value:
        userinfo, value = value.rsplit("@", 1)
        if ":" in userinfo:
            embedded_user, embedded_pass = userinfo.split(":", 1)
        else:
            embedded_user = userinfo
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit():
        return None
    user = embedded_user or username
    pw = embedded_pass or password
    auth = f"{quote(user, safe='')}:{quote(pw, safe='')}@" if user else ""
    return f"{scheme}://{auth}{host}:{port}"


def parse_proxy_list(raw: str, username: str = "", password: str = "") -> list[str]:
    """Parse a comma/space/newline separated proxy list into proxy URLs.

    Invalid entries are dropped silently; an empty or whitespace-only input
    returns ``[]`` (callers treat that as "proxy tier disabled").
    """
    urls: list[str] = []
    for token in raw.replace(",", " ").split():
        url = build_proxy_url(token, username, password)
        if url is not None:
            urls.append(url)
    return urls


def _extract_host(url: str) -> str:
    """Host part of a proxy URL for safe logging (never credentials)."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        host = None
    return host or url


@dataclass
class _ProxyEntry:
    """One proxy with its failure bookkeeping (cooldown tracking)."""

    host: str
    url: str
    failed_at: float = 0.0  # monotonic timestamp of last failure; 0 = healthy
    fail_count: int = 0


class ProxyRotator:
    """Round-robin residential proxy rotator with per-proxy failure cooldown.

    ``enabled`` is False when no proxies are configured; in that state
    ``get_rotating_proxy()`` returns None and ``get_client()`` builds a
    direct (proxy-less) client.
    """

    def __init__(
        self,
        *,
        proxy_list: str | None = None,
        username: str | None = None,
        password: str | None = None,
        failure_cooldown: float = FAILURE_COOLDOWN,
    ) -> None:
        if proxy_list is None:
            proxy_list = os.getenv(PROXY_LIST_ENV, "")
        if username is None:
            username = os.getenv(PROXY_USER_ENV, "")
        if password is None:
            password = os.getenv(PROXY_PASS_ENV, "")
        self._failure_cooldown = failure_cooldown
        self._lock = threading.Lock()
        urls = parse_proxy_list(proxy_list, username, password)
        self._entries: list[_ProxyEntry] = [_ProxyEntry(host=_extract_host(u), url=u) for u in urls]
        self._index = 0
        self.enabled = bool(self._entries)
        self.proxy_count = len(self._entries)

    # ── Rotation ─────────────────────────────────────────────────────

    def get_rotating_proxy(self) -> str | None:
        """Return the next healthy proxy URL (round-robin), or None.

        Proxies on failure cooldown are skipped. None is returned when the
        rotator is disabled or every proxy is on cooldown — callers should
        then fall back to direct.
        """
        if not self._entries:
            return None
        now = time.monotonic()
        with self._lock:
            count = len(self._entries)
            for _ in range(count):
                entry = self._entries[self._index % count]
                self._index += 1
                if entry.failed_at == 0 or now - entry.failed_at >= self._failure_cooldown:
                    return entry.url
        return None

    def mark_failed(self, proxy: str | None) -> None:
        """Record a failure for ``proxy`` so it is skipped until cooldown expires.

        No-op when ``proxy`` is None or does not match a known entry. Only the
        host is logged — the URL (which may embed credentials) is never logged.
        """
        if not proxy:
            return
        now = time.monotonic()
        with self._lock:
            for entry in self._entries:
                if entry.url == proxy:
                    entry.failed_at = now
                    entry.fail_count += 1
                    log.debug("proxy_marked_failed", host=entry.host, fail_count=entry.fail_count)
                    return

    def next_after_failure(self, proxy: str | None = None) -> str | None:
        """Mark ``proxy`` failed (if given) and return the next healthy proxy.

        Shorthand for the fallback cascade: after a failure, cooldown the bad
        proxy and pick the next one. Returns None when all proxies are down so
        the caller can fall back to direct.
        """
        if proxy is not None:
            self.mark_failed(proxy)
        return self.get_rotating_proxy()

    # ── Client factory ───────────────────────────────────────────────

    def get_client(self, *, proxy: object = _AUTO_PROXY) -> httpx.AsyncClient:
        """Build an httpx client routed through ``proxy``.

        Omit ``proxy`` to auto-pick the next rotating proxy; pass
        ``proxy=None`` to force direct regardless of configuration; pass a
        proxy URL string to pin a specific proxy. When the rotator is disabled
        (or every proxy is on cooldown) the auto-pick result is None, so the
        client is direct.
        """
        proxy_url = self.get_rotating_proxy() if proxy is _AUTO_PROXY else cast("str | None", proxy)
        return httpx.AsyncClient(
            proxy=proxy_url,
            timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=DEFAULT_CONNECT_TIMEOUT),
            follow_redirects=True,
        )


# ── Singleton ────────────────────────────────────────────────────────

_rotator: ProxyRotator | None = None
_rotator_lock = threading.Lock()


def get_proxy_rotator() -> ProxyRotator:
    """Return the process-wide :class:`ProxyRotator` (lazy, env read once)."""
    global _rotator
    if _rotator is None:
        with _rotator_lock:
            if _rotator is None:
                _rotator = ProxyRotator()
    return _rotator


def reset_proxy_rotator() -> None:
    """Drop the cached singleton so env vars are re-read (tests / rotation)."""
    global _rotator
    with _rotator_lock:
        _rotator = None
