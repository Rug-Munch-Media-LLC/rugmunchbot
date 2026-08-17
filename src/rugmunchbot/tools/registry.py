"""Async tool registry for RugMunchBot — the zero-fabrication data layer.

Every factual market claim in the bot's chat answers must come from a tool
in this registry. Each tool:
  * makes a REAL API call at query time (async httpx),
  * is wrapped with a timeout + one retry,
  * is cached for a short TTL (default 60s) so repeated questions stay fast,
  * returns a :class:`ToolResult` carrying the symbols/addresses it actually
    saw — the guardrail uses those sets to strip fabricated tickers from the
    LLM answer (see ``tools/guardrails.py``).

No tool ever synthesizes data. If the underlying source is down / rate-limited
the tool returns ``ok=False`` and the caller must answer "I don't have live
data for that right now" instead of guessing.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._cache import TTLCache

logger = get_logger("rmi_bot.tools")

# Type of a tool handler: receives kwargs, returns ToolResult.
ToolHandler = Callable[..., Awaitable["ToolResult"]]

DEFAULT_TOOL_TIMEOUT = 6.0
DEFAULT_TOOL_TTL = 60.0
MAX_RETRIES = 1

# Failures (``ok=False``) are cached for a SHORT window so a transient
# timeout / rate-limit does not poison the full TTL: a 30s backend blip would
# otherwise make ``scanner_intel`` report "no live data" for the full 300s
# policy TTL and ``price_lookup`` for 60s. 5-10s negative window (caching
# audit); 8s chosen as a middle ground between retry frequency and
# source-hammering.
NEGATIVE_TTL = 8.0


def _negative_namespace(name: str) -> str:
    """Cache namespace for transient failures.

    Deliberately NOT a key in ``budget.TTL_POLICY``: stable-data overrides
    live there, and ``effective_ttl`` would promote a short negative TTL back
    up to the tool's full policy TTL (e.g. ``scanner_intel`` 300s) — the very
    poisoning the negative cache exists to prevent. The ``:neg`` suffix keeps
    this namespace out of the policy so failures really expire after
    ``NEGATIVE_TTL`` seconds.
    """
    return f"{name}:neg"


@dataclass
class ToolResult:
    """Structured result of a tool execution.

    Attributes:
        tool: Tool name (e.g. "price_lookup").
        ok: True when a live source produced data (or a cached copy of one).
        data: The REAL structured payload from the source (dict or list).
        symbols: Token symbols that actually appeared in the source data.
        addresses: On-chain addresses that appeared in the source data.
        source: Human-readable source label (endpoint / provider).
        cached: True when served from the TTL cache.
        error: Failure reason when ok=False (source down / rate-limited).
        latency_ms: Wall-clock time of the underlying call.
    """

    tool: str
    ok: bool
    data: Any = None
    symbols: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    source: str = ""
    cached: bool = False
    error: str | None = None
    latency_ms: float = 0.0

    @property
    def symbol_set(self) -> set[str]:
        """Uppercased, de-duplicated set of symbols seen by the tool."""
        return {s.upper() for s in self.symbols if s}

    @property
    def address_set(self) -> set[str]:
        """De-duplicated set of addresses seen by the tool."""
        return {a for a in self.addresses if a}

    def summary(self, limit: int = 400) -> str:
        """Compact JSON-ish summary for prompt injection / logging."""
        import json

        payload = self.data if isinstance(self.data, (dict, list)) else {"value": self.data}
        try:
            text = json.dumps(payload, default=str)[:limit]
        except (TypeError, ValueError):
            text = str(payload)[:limit]
        return text


class ToolRegistry:
    """Register named tools and execute them with timeout/retry/TTL cache.

    Usage::

        registry = ToolRegistry()
        registry.register("price_lookup", my_price_handler, timeout=6.0, ttl=60.0)
        result = await registry.run("price_lookup", address="0x...")
    """

    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}
        self._timeouts: dict[str, float] = {}
        self._ttls: dict[str, float] = {}
        self._cache: TTLCache[ToolResult] = TTLCache()

    def register(
        self, name: str, handler: ToolHandler, *, timeout: float = DEFAULT_TOOL_TIMEOUT, ttl: float = DEFAULT_TOOL_TTL
    ) -> None:
        """Register a tool handler. ``timeout`` bounds each call; ``ttl`` is
        the cache lifetime in seconds (0 disables caching)."""
        if name in self._handlers:
            logger.warning("tool %s re-registered — replacing handler", name)
        self._handlers[name] = handler
        self._timeouts[name] = timeout
        self._ttls[name] = ttl

    def __contains__(self, name: str) -> bool:
        return name in self._handlers

    @property
    def names(self) -> list[str]:
        """Sorted names of all registered tools."""
        return sorted(self._handlers)

    def cache_key(self, name: str, kwargs: dict[str, Any]) -> str:
        """Stable cache key from the tool name + normalized kwargs."""
        items = []
        for k in sorted(kwargs):
            v = kwargs[k]
            items.append(f"{k}={v}")
        return "|".join(items)

    async def run(self, name: str, **kwargs: Any) -> ToolResult:
        """Execute ``name`` with kwargs. Never raises: returns ok=False on
        failure so callers degrade to the "no live data" message."""
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(tool=name, ok=False, error=f"unknown tool: {name}")
        timeout = self._timeouts.get(name, DEFAULT_TOOL_TIMEOUT)
        ttl = self._ttls.get(name, DEFAULT_TOOL_TTL)

        key = self.cache_key(name, kwargs)
        if ttl > 0:
            cached = self._cache.get(name, key)
            if cached is None:
                # A failure cached by an earlier run lives in the negative
                # namespace (short TTL) — serve it so we don't re-hammer the
                # down source, but only for the short negative window.
                cached = self._cache.get(_negative_namespace(name), key)
            if cached is not None:
                cached.cached = True
                return cached

        result = await self._run_with_retry(name, handler, timeout, **kwargs)
        if ttl > 0:
            if result.ok:
                self._cache.set(name, key, result, ttl)
            else:
                # Negative cache: a transient failure must NOT occupy the full
                # tool TTL. Store it under the ``:neg`` namespace with a short
                # TTL so the next call retries the live source after ~8s.
                self._cache.set(_negative_namespace(name), key, result, NEGATIVE_TTL)
        return result

    async def _run_with_retry(self, name: str, handler: ToolHandler, timeout: float, **kwargs: Any) -> ToolResult:
        last: ToolResult | None = None
        for attempt in range(MAX_RETRIES + 1):
            start = time.perf_counter()
            try:
                result = await asyncio.wait_for(handler(**kwargs), timeout=timeout)
                if not isinstance(result, ToolResult):
                    result = ToolResult(tool=name, ok=bool(result), data=result)
                result.latency_ms = (time.perf_counter() - start) * 1000
                result.tool = name
                return result
            except (TimeoutError, Exception) as exc:
                last = ToolResult(tool=name, ok=False, error=f"{type(exc).__name__}: {exc}"[:200])
                logger.debug("tool %s attempt %d failed: %s", name, attempt + 1, exc)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.3 * (attempt + 1))
        last = last or ToolResult(tool=name, ok=False, error="tool failed")
        last.latency_ms = (time.perf_counter() - start) * 1000
        return last

    async def run_many(self, names: list[str], **kwargs: Any) -> dict[str, ToolResult]:
        """Run several tools concurrently. Returns {name: result}; a tool
        that was never registered yields ok=False."""
        tasks = {name: self.run(name, **kwargs) for name in names}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        out: dict[str, ToolResult] = {}
        for name, res in zip(tasks, results, strict=False):
            if isinstance(res, BaseException):
                out[name] = ToolResult(tool=name, ok=False, error=str(res))
            else:
                out[name] = res
        return out

    async def run_many_kwargs(self, spec: dict[str, dict[str, Any]], **shared: Any) -> dict[str, ToolResult]:
        """Run several tools concurrently, each with its OWN kwargs.

        ``spec`` maps tool name -> per-tool kwargs; ``shared`` is merged into
        every tool's kwargs. Returns {name: result} in the same shape as
        :meth:`run_many`.

        Shared context kwargs (e.g. ``history`` forwarded from the chat layer)
        are ONLY passed to handlers that accept them — a handler whose
        signature declares the parameter by name or a var-keyword catch-all
        (``**_ctx`` / ``**kwargs``). Strict-signature handlers get only their
        per-tool kwargs, so an unknown shared kwarg can never raise
        ``TypeError`` inside the handler (which the registry would swallow
        into ``ok=False`` — a live data miss for every tool on chat paths
        that forward ``history``).
        """
        tasks: dict[str, Any] = {}
        for name, tool_kwargs in spec.items():
            handler = self._handlers.get(name)
            merged = self._accepted_kwargs(handler, tool_kwargs, shared)
            tasks[name] = self.run(name, **merged)
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        out: dict[str, ToolResult] = {}
        for name, res in zip(tasks, results, strict=False):
            if isinstance(res, BaseException):
                out[name] = ToolResult(tool=name, ok=False, error=str(res))
            else:
                out[name] = res
        return out

    @staticmethod
    def _accepted_kwargs(
        handler: ToolHandler | None, tool_kwargs: dict[str, Any], shared: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge per-tool kwargs with shared ctx, keeping accepted params.

        Shared context (``shared``) is forwarded only when the handler can
        actually receive it: it declares the param by name, or it has a
        var-keyword parameter (``**_ctx`` / ``**kwargs``). Otherwise the
        shared kwargs are dropped and only the router's per-tool kwargs are
        passed — a strict-signature handler (e.g. ``price_lookup``) must
        never see ``history=...``.

        When the signature cannot be inspected (rare) the conservative
        choice wins: shared kwargs are NOT forwarded, because we cannot
        verify the handler tolerates them.
        """
        merged = {**shared, **tool_kwargs}
        if handler is None:
            return merged
        try:
            sig = inspect.signature(handler)
        except (TypeError, ValueError):
            return dict(tool_kwargs)
        for param in sig.parameters.values():
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                return merged
        return {k: v for k, v in merged.items() if k in sig.parameters}

    def clear_cache(self, namespace: str | None = None) -> None:
        """Evict cached tool results, optionally scoped to ``namespace``."""
        self._cache.clear(namespace)


# ── Shared helper: backend JSON GET with the bot's auth header ─────────────
async def backend_json_get(
    path: str, *, params: dict[str, Any] | None = None, timeout: float = 6.0
) -> dict[str, Any] | None:
    """GET a backend (own-data) endpoint with the shared-secret header."""
    from app.domains.telegram.rugmunchbot.config import BACKEND_URL, backend_headers
    from app.domains.telegram.rugmunchbot.tools._client import http_get_json

    return await http_get_json(
        f"{BACKEND_URL}{path}",
        params=params,
        headers=backend_headers(),
        timeout=timeout,
    )


async def backend_json_post(
    path: str, *, json: dict[str, Any] | None = None, timeout: float = 8.0
) -> dict[str, Any] | None:
    """POST a backend (own-data) endpoint with the shared-secret header."""
    from app.domains.telegram.rugmunchbot.config import BACKEND_URL, backend_headers
    from app.domains.telegram.rugmunchbot.tools._client import http_post_json

    return await http_post_json(
        f"{BACKEND_URL}{path}",
        json=json,
        headers=backend_headers(),
        timeout=timeout,
    )
