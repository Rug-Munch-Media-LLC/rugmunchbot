"""RugMunchBot tool registry — REAL data layer for zero-fabrication chat.

Public surface:
  * ``registry`` — the shared ToolRegistry with all tools registered.
  * ``run_for_message(text, **ctx)`` — route + run tools for a user question,
    returning the raw ToolResults plus the LIVE DATA prompt block.
  * ``guardrails.sanitize_answer`` — post-processing fabrication guardrail.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.registry import ToolRegistry, ToolResult

logger = get_logger("rmi_bot.tools")

# Shared registry instance (module-level so the bot, handlers and tests use
# the same TTL cache).
registry = ToolRegistry()


def _register_defaults() -> None:
    """Register every tool once. Idempotent-ish: registration replaces
    handlers, so repeated imports do not duplicate anything."""
    # Self-registering intelligence tools — each module owns its register_tool
    # (name, handler, timeout, TTL). Additive imports, safe for parallel agents.
    from rugmunchbot.tools.birdeye import birdeye_market
    from rugmunchbot.tools.market import market_overview
    from rugmunchbot.tools.market_condition import market_condition
    from rugmunchbot.tools.onchain import smart_money, top_traders
    from rugmunchbot.tools.prediction_markets import register_tool as _register_prediction_markets
    from rugmunchbot.tools.price import price_lookup
    from rugmunchbot.tools.rag_lookup import rag_lookup
    from rugmunchbot.tools.security import security_scan, wallet_analysis
    from rugmunchbot.tools.story_fetch import story_fetch
    from rugmunchbot.tools.ta import register_tool as _register_ta
    from rugmunchbot.tools.trend_algo import register_tool as _register_trend_algo
    from rugmunchbot.tools.trending import news_headlines, trending
    from rugmunchbot.tools.web import web_search

    registry.register("price_lookup", price_lookup, timeout=6.0, ttl=60.0)
    registry.register("trending", trending, timeout=6.0, ttl=120.0)
    registry.register("news_headlines", news_headlines, timeout=6.0, ttl=120.0)
    registry.register("top_traders", top_traders, timeout=8.0, ttl=120.0)
    registry.register("smart_money", smart_money, timeout=8.0, ttl=120.0)
    registry.register("security_scan", security_scan, timeout=8.0, ttl=60.0)
    registry.register("wallet_analysis", wallet_analysis, timeout=20.0, ttl=60.0)
    registry.register("web_search", web_search, timeout=12.0, ttl=300.0)
    # Pull any story from any site → clean text + title/author/date. ttl=0:
    # URL content is NOT cached (fresh fetch per call); bounded by timeout.
    registry.register("story_fetch", story_fetch, timeout=14.0, ttl=0.0)
    registry.register("birdeye_market", birdeye_market, timeout=10.0, ttl=120.0)
    registry.register("market_overview", market_overview, timeout=10.0, ttl=60.0)
    registry.register("market_condition", market_condition, timeout=10.0, ttl=120.0)
    # Direct RAG knowledge lookup (knowledge questions): bounded + fail-open.
    # Registered but NOT routed by tools/intent.py — chat_ai runs it explicitly
    # so the backend auto-RAG stays authoritative on tier-1.
    registry.register("rag_lookup", rag_lookup, timeout=8.0, ttl=300.0)

    _register_trend_algo(registry)
    _register_prediction_markets(registry)
    _register_ta(registry)


_register_defaults()
from rugmunchbot.tools import scanner as _scanner_extra  # noqa: E402, F401, I001 — self-registers scanner_intel + token_meta (single additive import; must run after `registry` exists)


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
    from rugmunchbot.tools.intent import route_tools, tool_kwargs

    names = route_tools(text)
    if not names:
        return {}

    per_tool = tool_kwargs(text, names)
    return await registry.run_many_kwargs(per_tool, **ctx)


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
    "known_addresses",
    "known_symbols",
    "registry",
    "run_for_message",
]
