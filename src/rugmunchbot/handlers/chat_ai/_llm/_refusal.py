"""No-source refusal — the deterministic floor under live-data answers.

``build_tool_prompt_block`` ASKS the model to admit when it has no live data.
An instruction is not a guarantee: when the price/scan/trending source is down
the model still answers a price question from training memory, and the
existing guardrail only labels such figures "⚠ unverified" (or, worse, the
ticker is stripped and the sentence reads as fact).

This module ENFORCES the refusal before the LLM is called: when the question
routes to a live-data tool and NOT ONE of those tools returned real data, the
turn answers with the shared ``NO_LIVE_DATA_MESSAGE`` instead of a guess.

Deliberately narrow:

* Knowledge / conceptual questions route to no live-data tool → never refused
  (the free-model fallback floor keeps answering them).
* ``web_search``'s synthesised knowledge fallback and ``rag_lookup`` are NOT
  live sources for a price or a risk score — they cannot satisfy the gate,
  exactly as in ``_guards._post_guardrail``'s ``tools_ok`` rule.
* Any one live tool returning ``ok=True`` removes the refusal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rugmunchbot.tools.guardrails import NO_LIVE_DATA_MESSAGE

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rugmunchbot.tools.registry import ToolResult

# Tools that assert a LIVE MARKET FACT. Only these trigger the refusal.
_LIVE_DATA_TOOLS = frozenset(
    {
        "price_lookup",
        "security_scan",
        "trending",
        "trend_by_chain",
        "token_dna",
        "rug_prediction",
        "volume_integrity",
        "smart_money",
        "top_traders",
        "birdeye_market",
        "market_overview",
        "market_condition",
        "wallet_analysis",
    }
)


def _is_live(result: ToolResult) -> bool:
    """True when the result is REAL fetched data (not a synthesised fallback)."""
    if not result.ok:
        return False
    return not (result.tool == "web_search" and str(result.source).startswith("knowledge"))


def no_source_refusal(text: str, results: Mapping[str, ToolResult]) -> str | None:
    """The refusal message when a live-data question has no live source.

    Args:
        text: The user's question (routed with the same deterministic router
            the pipeline uses, so the decision matches the tools actually run).
        results: Every tool result gathered for this turn (may be empty).

    Returns:
        ``None`` when the turn may proceed to the LLM, else the exact
        ``NO_LIVE_DATA_MESSAGE`` the rest of the pipeline already uses.
    """
    from rugmunchbot.tools.intent import route_tools

    routed = {name for name in route_tools(text) if name in _LIVE_DATA_TOOLS}
    if not routed:
        return None
    # Only a LIVE result for a tool the question actually routed to satisfies
    # the gate — a concurrent smart-money fetch must never vouch for a price.
    if any(_is_live(results[name]) for name in routed if name in results):
        return None
    return NO_LIVE_DATA_MESSAGE


__all__ = ["no_source_refusal"]
