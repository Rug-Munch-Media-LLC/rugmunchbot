"""Per-provider API budget + rate-limit tracker for RugMunchBot tools.

Every external HTTP call the tool layer makes is charged to a provider
bucket (dexscreener, coingecko, birdeye, backend, defillama, alternative.me,
coincap, coinpaprika, searxng, bridge, ...). ``tools/_client.py`` gates every
``http_get_json`` / ``http_post_json`` call through this tracker BEFORE the
request is made, so a provider that is over its per-minute cap degrades to a
"miss" (``None``) — and the tool's own fallback chain ("one source first,
fallback only on miss") moves on to the next provider instead of hammering
the rate-limited one. Providers are never called speculatively in parallel:
a tool tries its primary source and only falls back when that source misses.

Policy (Frugality Principle, AGENTS.md — free APIs first, never torch credits):
  * caps calls per provider per minute (soft budget — over-budget calls are
    NOT made; they count as misses and trigger the next fallback source),
  * dedupes identical queries through the TTL cache (see ``TTL_POLICY`` —
    stable data lives longer: prices 60s, trending 300s, market-overview 600s),
  * accumulates per-provider call counters so credit burn is visible in logs
    (``budget.summary()`` / ``maybe_log_summary()``).

Thread-safety: the bot runs a single asyncio event loop and every access
happens on that loop, so plain dicts + deques are sufficient.
"""

from __future__ import annotations

import time
from collections import deque

from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.budget")

# ══════════════════════════════════════════════════════════════════════════
# PER-PROVIDER PER-MINUTE CAPS
# Free-tier ceilings from AGENTS.md / Frugality Principle. These are SOFT
# budgets: when the cap is hit the call is skipped (miss) and the tool falls
# back, so a single busy question can never exhaust a free quota for everyone.
# ══════════════════════════════════════════════════════════════════════════
PROVIDER_LIMITS: dict[str, int] = {
    "dexscreener": 60,  # free, 300 req/min ceiling — stay well under
    "coingecko": 15,  # free tier ~10-30 req/min (no key)
    "birdeye": 10,  # free tier exhausted 2026-08-14 [REVIEW] — keep tiny
    "coinpaprika": 20,  # free, no key
    "coincap": 20,  # free, no key
    "defillama": 60,  # generous free tier
    "alternative": 30,  # free, 10 req/s
    "goplus": 30,  # free, 10 req/s
    "honeypot": 30,  # free, no auth
    "solanafm": 20,  # free tier
    "backend": 120,  # our own API — soft ceiling, own infra
    "searxng": 60,  # self-hosted, zero cost
    "bridge": 30,  # local Chat RAG Bridge (port 8095)
    "unknown": 20,  # anything unmapped — be conservative
}
DEFAULT_PROVIDER_LIMIT = 20

# Known public hosts → provider bucket. Local infra is handled by port below.
_HOST_PROVIDERS: dict[str, str] = {
    "api.dexscreener.com": "dexscreener",
    "api.coingecko.com": "coingecko",
    "public-api.birdeye.so": "birdeye",
    "api.coinpaprika.com": "coinpaprika",
    "api.coincap.io": "coincap",
    "api.llama.fi": "defillama",
    "api.alternative.me": "alternative",
    "api.gopluslabs.com": "goplus",
    "api.honeypot.is": "honeypot",
    "api.solana.fm": "solanafm",
    "rmi-backend": "backend",  # in-Docker backend
}
# Localhost ports → provider bucket (backend is the default local host).
_LOCAL_PORTS: dict[str, str] = {
    "8095": "bridge",  # BRIDGE_CONTEXT_ENDPOINT
    "8889": "searxng",  # SEARXNG_URL
}


def provider_for_url(url: str, provider: str | None = None) -> str:
    """Map a URL (or an explicit provider name) to a budget bucket.

    An explicit ``provider`` wins (callers that know their source pass it);
    otherwise the URL authority decides. Host extraction is string-based on
    purpose — no ``urllib`` import, because the tool-layer AST gate forbids
    blocking stdlib net imports.
    """
    if provider:
        return provider.strip().lower()
    host, port = _authority(url)
    mapped = _HOST_PROVIDERS.get(host)
    if mapped:
        return mapped
    if host in ("127.0.0.1", "localhost"):
        return _LOCAL_PORTS.get(port, "backend")
    return "unknown"


def _authority(url: str) -> tuple[str, str]:
    """Return ``(host, port)`` from a URL — string ops only."""
    rest = url.split("://", 1)[1] if "://" in url else url
    authority = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if ":" in authority:
        host, _, port = authority.rpartition(":")
        return host.lower(), port
    return authority.lower(), ""


# ══════════════════════════════════════════════════════════════════════════
# STABLE-DATA TTL POLICY
# Longer TTLs for data that barely moves, so identical questions are served
# from the TTL cache instead of re-hitting the API. Applied by
# ``tools/_cache.py`` on every ``TTLCache.set`` for a known tool namespace.
# ══════════════════════════════════════════════════════════════════════════
TTL_POLICY: dict[str, float] = {
    "price_lookup": 60.0,  # prices move — 60s keeps answers fresh
    "trending": 300.0,  # trending lists are stable over minutes
    "market_overview": 600.0,  # market-wide picture is stable ~10 min
    "news_headlines": 300.0,
    "top_traders": 300.0,
    "smart_money": 300.0,
    "security_scan": 60.0,
    "wallet_analysis": 60.0,
    "web_search": 300.0,
    "birdeye_market": 120.0,
    "rag_lookup": 300.0,
    "scanner_intel": 300.0,
    "token_meta": 300.0,
}


def effective_ttl(namespace: str, ttl: float) -> float:
    """TTL for a tool namespace: the policy wins for known stable data,
    otherwise the caller-provided default is kept."""
    return TTL_POLICY.get(namespace, ttl)


# ══════════════════════════════════════════════════════════════════════════
# BUDGET TRACKER
# ══════════════════════════════════════════════════════════════════════════
_WINDOW_SECONDS = 60.0


class BudgetTracker:
    """Per-provider per-minute call budget + cumulative credit counters."""

    def __init__(self) -> None:
        self._window: dict[str, deque[float]] = {}
        self._totals: dict[str, int] = {}
        self._misses: dict[str, int] = {}

    # ── internals ──────────────────────────────────────────────────────────
    def _queue(self, provider: str) -> deque[float]:
        q = self._window.get(provider)
        if q is None:
            q = deque()
            self._window[provider] = q
        return q

    def _prune(self, provider: str, now: float) -> None:
        q = self._queue(provider)
        while q and now - q[0] > _WINDOW_SECONDS:
            q.popleft()

    # ── reads ──────────────────────────────────────────────────────────────
    def limit(self, provider: str) -> int:
        """Per-minute cap for a provider."""
        return PROVIDER_LIMITS.get(provider, DEFAULT_PROVIDER_LIMIT)

    def window_count(self, provider: str) -> int:
        """Calls recorded for a provider within the current 60s window."""
        self._prune(provider, time.monotonic())
        return len(self._queue(provider))

    def total_count(self, provider: str) -> int:
        """Cumulative calls to a provider since process start (credit burn)."""
        return self._totals.get(provider, 0)

    def miss_count(self, provider: str) -> int:
        """Cumulative calls skipped because the provider was over budget."""
        return self._misses.get(provider, 0)

    def total_recorded(self) -> int:
        """Cumulative calls across all providers."""
        return sum(self._totals.values())

    def summary(self) -> dict[str, dict[str, int]]:
        """Per-provider credit-burn snapshot for logging / admin visibility."""
        out: dict[str, dict[str, int]] = {}
        providers = sorted(set(self._totals) | set(self._misses) | set(self._window))
        for p in providers:
            out[p] = {
                "window": self.window_count(p),
                "total": self.total_count(p),
                "misses": self.miss_count(p),
                "limit": self.limit(p),
            }
        return out

    # ── gate ───────────────────────────────────────────────────────────────
    def acquire(self, provider: str) -> bool:
        """Gate ONE provider call. Returns True when within budget (the call
        is counted immediately); False when the per-minute cap is exhausted
        (the caller treats it as a miss and falls back to another source)."""
        bucket = provider or "unknown"
        now = time.monotonic()
        self._prune(bucket, now)
        if len(self._queue(bucket)) >= self.limit(bucket):
            self._misses[bucket] = self._misses.get(bucket, 0) + 1
            return False
        self._queue(bucket).append(now)
        self._totals[bucket] = self._totals.get(bucket, 0) + 1
        return True

    def reset(self) -> None:
        """Clear all counters (test helper)."""
        self._window.clear()
        self._totals.clear()
        self._misses.clear()


# Module-level singleton — every tool HTTP call goes through this instance.
budget = BudgetTracker()

_LOG_EVERY_CALLS = 100


def maybe_log_summary() -> None:
    """Log cumulative per-provider counts every ``_LOG_EVERY_CALLS`` calls.

    Gives credit-burn visibility in production logs without per-call spam:
    the event name is ``tool_api_budget`` and carries the full
    ``budget.summary()`` payload.
    """
    recorded = budget.total_recorded()
    if recorded and recorded % _LOG_EVERY_CALLS == 0:
        logger.info("tool_api_budget", calls=recorded, by_provider=budget.summary())


__all__ = [
    "DEFAULT_PROVIDER_LIMIT",
    "PROVIDER_LIMITS",
    "TTL_POLICY",
    "BudgetTracker",
    "budget",
    "effective_ttl",
    "maybe_log_summary",
    "provider_for_url",
]
