"""RugMunchBot tool registry — REAL data layer for zero-fabrication chat.

Public surface:
  * ``get_registry()`` — build (once) the shared ToolRegistry with all tools
    registered. ``registry`` (backward-compat) resolves to the same instance
    via module ``__getattr__`` on first access.
  * ``run_for_message(text, **ctx)`` — route + run tools for a user question,
    returning the raw ToolResults plus the LIVE DATA prompt block.
  * ``guardrails.sanitize_answer`` — post-processing fabrication guardrail.

Importing this package is side-effect-free: the ToolRegistry instance and the
~20 tool modules are only imported/registered on first ``registry`` access
(``get_registry()`` or ``from ...tools import registry``).
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools.registry import ToolRegistry, ToolResult

# The from-import above registers the ``tools.registry`` SUBMODULE as an
# attribute of this package, which would shadow the lazy ``registry`` API
# (``from ...tools import registry`` prefers the submodule — the import
# machinery imports it before attribute lookup). Pop the submodule binding so
# ``registry`` attribute access resolves via module ``__getattr__`` (PEP 562)
# to the shared ToolRegistry instance. The submodule itself stays importable
# via its own path (``from ...tools.registry import ToolResult`` keeps working).
globals().pop("registry", None)

logger = get_logger("rmi_bot.tools")

_registry: ToolRegistry | None = None

# Type-only declaration for the lazy public ``registry`` name (resolved at
# runtime via module __getattr__ below). Keeps mypy's view of
# ``from ...tools import registry`` as ToolRegistry instead of the
# ``tools.registry`` submodule. NOTE: do NOT reference the ``registry`` global
# inside this module's own functions — module __getattr__ does NOT fire for
# function-scope globals (use get_registry()).
registry: ToolRegistry


def get_registry() -> ToolRegistry:
    """Return the shared ToolRegistry, building it on first access.

    The instance is assigned to ``_registry`` BEFORE ``_register_defaults``
    runs so self-registering tool modules (e.g. ``scanner``, which imports
    ``registry`` during its own import) resolve to the singleton instead of
    recursing into a second build.
    """
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_defaults(_registry)
        # Self-registers scanner_intel + token_meta (single additive import;
        # must run after `_registry` exists so its ``from ...tools import
        # registry`` resolves to the singleton above).
        from app.domains.telegram.rugmunchbot.tools import scanner as _scanner_extra  # noqa: F401
    return _registry


def _register_defaults(registry_: ToolRegistry) -> None:
    """Register every tool once on ``registry_``. Idempotent-ish: registration
    replaces handlers, so repeated imports do not duplicate anything."""
    # Self-registering intelligence tools — each module owns its register_tool
    # (name, handler, timeout, TTL). Additive imports, safe for parallel agents.
    from app.domains.telegram.rugmunchbot.tools.birdeye import birdeye_market
    from app.domains.telegram.rugmunchbot.tools.charting import register_tool as _register_charting
    from app.domains.telegram.rugmunchbot.tools.market import market_overview
    from app.domains.telegram.rugmunchbot.tools.market_condition import market_condition
    from app.domains.telegram.rugmunchbot.tools.onchain import smart_money, top_traders
    from app.domains.telegram.rugmunchbot.tools.pattern_memory import register_tool as _register_pattern_memory
    from app.domains.telegram.rugmunchbot.tools.prediction_markets import register_tool as _register_prediction_markets
    from app.domains.telegram.rugmunchbot.tools.price import price_lookup
    from app.domains.telegram.rugmunchbot.tools.rag_lookup import rag_lookup
    from app.domains.telegram.rugmunchbot.tools.security import security_scan, wallet_analysis
    from app.domains.telegram.rugmunchbot.tools.story_fetch import story_fetch
    from app.domains.telegram.rugmunchbot.tools.ta import register_tool as _register_ta
    from app.domains.telegram.rugmunchbot.tools.trend_algo import register_tool as _register_trend_algo
    from app.domains.telegram.rugmunchbot.tools.trending import news_headlines, trending
    from app.domains.telegram.rugmunchbot.tools.web import web_search

    registry_.register("price_lookup", price_lookup, timeout=6.0, ttl=60.0)
    registry_.register("trending", trending, timeout=6.0, ttl=120.0)
    registry_.register("news_headlines", news_headlines, timeout=6.0, ttl=120.0)
    registry_.register("top_traders", top_traders, timeout=8.0, ttl=120.0)
    registry_.register("smart_money", smart_money, timeout=8.0, ttl=120.0)
    registry_.register("security_scan", security_scan, timeout=8.0, ttl=60.0)
    registry_.register("wallet_analysis", wallet_analysis, timeout=20.0, ttl=60.0)
    # web_search timeout is generous (75s): when the bridge + SearXNG are
    # down the tool synthesizes a knowledge answer through the remote LLM
    # chain (snappy tier → deep tier → DeepSeek Flash API, up to ~50s) so
    # it NEVER hard-fails. The chat pipeline bounds its own wait on this
    # tool (chat_ai LIVE_TOOLS_WAIT) so the slow fallback can't stall
    # answers.
    registry_.register("web_search", web_search, timeout=75.0, ttl=300.0)
    # Pull any story from any site → clean text + title/author/date. ttl=0:
    # URL content is NOT cached (fresh fetch per call); bounded by timeout.
    registry_.register("story_fetch", story_fetch, timeout=14.0, ttl=0.0)
    registry_.register("birdeye_market", birdeye_market, timeout=10.0, ttl=120.0)
    registry_.register("market_overview", market_overview, timeout=10.0, ttl=60.0)
    registry_.register("market_condition", market_condition, timeout=10.0, ttl=120.0)
    # Direct RAG knowledge lookup (knowledge questions): bounded + fail-open.
    # Registered but NOT routed by tools/intent.py — chat_ai runs it explicitly
    # so the backend auto-RAG stays authoritative on tier-1.
    registry_.register("rag_lookup", rag_lookup, timeout=8.0, ttl=300.0)

    _register_trend_algo(registry_)
    _register_prediction_markets(registry_)
    _register_ta(registry_)
    _register_charting(registry_)
    _register_pattern_memory(registry_)


def __getattr__(name: str) -> Any:
    """Lazily resolve the shared ``registry`` (PEP 562, backward-compat).

    ``from ...tools import registry`` keeps working unchanged; the first
    access builds (and caches) the registry inside :func:`get_registry`.
    """
    if name == "registry":
        return get_registry()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def run_for_message(text: str, **ctx: Any) -> dict[str, ToolResult]:
    """Route + run tools for a user question.

    ``route_tools`` selects the tools (deterministic regex/keyword intent
    routing) and ``tool_kwargs`` maps each selected tool to the address /
    symbol / chain / kind kwargs it understands. Extra ``**ctx`` kwargs (e.g.
    ``history``) are forwarded to every tool.

    Args:
        text: The user's question.
        **ctx: Extra kwargs forwarded to tools (e.g. ``history``).

    Returns:
        ``{tool_name: ToolResult}`` for every tool the router selected.
    """
    from app.domains.telegram.rugmunchbot.tools.intent import route_tools, tool_kwargs

    names = route_tools(text)
    if not names:
        return {}

    per_tool = tool_kwargs(text, names)
    return await get_registry().run_many_kwargs(per_tool, **ctx)


def known_symbols(results: dict[str, ToolResult]) -> set[str]:
    """Union of every symbol the tools actually returned."""
    out: set[str] = set()
    for r in results.values():
        out |= r.symbol_set
    return out


def known_addresses(results: dict[str, ToolResult]) -> set[str]:
    """Union of every address the tools actually returned."""
    out: set[str] = set()
    for r in results.values():
        out |= r.address_set
    return out


__all__ = [
    "ToolRegistry",
    "ToolResult",
    "get_registry",
    "known_addresses",
    "known_symbols",
    "registry",
    "run_for_message",
]
