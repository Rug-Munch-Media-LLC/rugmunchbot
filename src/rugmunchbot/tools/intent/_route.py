"""Tool routing — which tools run for a question, and their kwargs."""

from __future__ import annotations

from typing import Any

from rugmunchbot.tools.classify import (
    extract_addresses,
    has_symbol,
    symbols_in,
)
from rugmunchbot.tools.intent._detect import (
    _excluded_tools,
    detect_risk_rank,
    extract_chain,
)
from rugmunchbot.tools.intent._vocab import (
    _CHART_WORDS,
    _MARKET_CONDITION_WORDS,
    _MARKET_OVERVIEW_WORDS,
    _NEWS_WORDS,
    _PATTERN_WORDS,
    _PICKS_WORDS,
    _PREDICT_TA_WORDS,
    _PREDICTION_WORDS,
    _PRICE_WORDS,
    _SECURITY_WORDS,
    _SMART_MONEY_WORDS,
    _TA_WORDS,
    _TRENDING_WORDS,
    _WALLET_WORDS,
)


def route_tools(text: str) -> list[str]:
    """Return the ordered list of tools to run for ``text``.

    Order matters: the highest-signal intent comes first. The caller may
    still run them concurrently; the order only drives prompt layout.

    Deterministic and cheap (regex/keyword only, no LLM). Tools that need an
    on-chain address (scanner_intel, token_meta, security_scan, top_traders,
    wallet_analysis) are only routed when an address is actually present —
    never route a tool that cannot answer.

    Explicit EXCLUSIONS ("don't show me trending") suppress their tools;
    pure NEGATIONS ("is $BONK not a honeypot?") do not (the data is still
    needed to answer). Multi-intent questions route several tools.
    """
    tools: list[str] = []
    addrs = extract_addresses(text)
    has_ticker = has_symbol(text)
    chain = extract_chain(text)
    excluded = _excluded_tools(text)

    # Trending: chain-filtered feed; when a chain is named OR the question
    # asks for risk-first picks ("riskiest/sketchiest"), also run the
    # per-chain trend_algo (transparent scoring over our data). risk_rank
    # makes that tool rank by RISK instead of momentum (rank kwarg below).
    # A bare risk-rank ask ("riskiest picks of the day") is an implicit
    # trending question when it names picks/tokens/coins.
    risk_rank = detect_risk_rank(text)
    trending_intent = bool(_TRENDING_WORDS.search(text)) or (risk_rank and bool(_PICKS_WORDS.search(text)))
    if trending_intent and "trending" not in excluded:
        tools.append("trending")
        if _NEWS_WORDS.search(text) and "news_headlines" not in excluded:
            tools.append("news_headlines")
        if chain or risk_rank:
            tools.append("trend_by_chain")

    if (has_ticker or _PRICE_WORDS.search(text)) and "price_lookup" not in excluded:
        tools.append("price_lookup")

    # Technical analysis: "what's the TA for $BONK" / "ta for $BONK" /
    # "technical analysis" / "rsi of btc". Routed on the phrase alone (like
    # price_lookup); without a symbol/address the tool degrades to ok=False
    # and the chat layer answers "no live data" — never a guess.
    if _TA_WORDS.search(text) and "technical_analysis" not in excluded:
        tools.append("technical_analysis")

    # Token-direction prediction questions ("will $BONK pump", "pump or
    # dump") surface the SAME real indicators + prediction read — never
    # polymarket event odds (that stays with prediction_markets).
    if _PREDICT_TA_WORDS.search(text) and "technical_analysis" not in excluded:
        tools.append("technical_analysis")

    # Price-action chart: "chart for $BONK" / "price chart" / "$BONK chart" —
    # the ASCII text chart + TA digest (tools/charting.py).
    if _CHART_WORDS.search(text) and "chart" not in excluded:
        tools.append("chart")

    # Pattern memory: "have we seen this pattern before" — recall similar
    # stored patterns from the Redis-backed store (zero-fabrication: only
    # returns patterns actually seen; degrades gracefully without a token).
    if _PATTERN_WORDS.search(text) and "pattern_recall" not in excluded:
        tools.append("pattern_recall")

    if _PREDICTION_WORDS.search(text) and "prediction_markets" not in excluded:
        tools.append("prediction_markets")

    if _MARKET_OVERVIEW_WORDS.search(text) and "market_overview" not in excluded:
        tools.append("market_overview")

    # Market-condition read: synthesized verdict for "how's the market right
    # now" / sentiment / bullish-bearish / risk on-off / good time to buy.
    if _MARKET_CONDITION_WORDS.search(text) and "market_condition" not in excluded:
        tools.append("market_condition")

    # Smart money / top traders: on-chain feeds + Birdeye (kind resolved in
    # tool_kwargs: top_traders needs a token address, smart_money does not).
    if _SMART_MONEY_WORDS.search(text):
        if addrs:
            tools.append("top_traders")
        tools.append("smart_money")
        tools.append("birdeye_market")

    security_excluded = {"scanner_intel", "security_scan", "token_meta"} & excluded
    if addrs and _SECURITY_WORDS.search(text) and not security_excluded:
        tools.append("scanner_intel")
        tools.append("security_scan")
        tools.append("token_meta")

    if addrs and _WALLET_WORDS.search(text) and "wallet_analysis" not in excluded:
        tools.append("wallet_analysis")

    # General knowledge / news / market context questions get live web context.
    if _NEWS_WORDS.search(text) and "trending" not in tools and "web_search" not in excluded:
        tools.append("web_search")

    # A bare address with no other intent is a security question.
    if addrs and "security_scan" not in tools and "wallet_analysis" not in tools and "security_scan" not in excluded:
        tools.append("security_scan")

    # Deduplicate preserving order.
    return list(dict.fromkeys(tools))


def tool_kwargs(text: str, names: list[str]) -> dict[str, dict[str, Any]]:
    """Per-tool kwargs map: each selected tool gets the params it understands.

    Mirrors the extraction ``run_for_message`` used to do inline (addresses,
    ``$SYMBOL`` mentions) plus chain + Birdeye ``kind``. Routing and the
    kwargs map live together here so a new tool needs exactly one change to
    become reachable AND correctly parameterized.

    Args:
        text: The user's question (source of extracted values).
        names: Tool names returned by :func:`route_tools`.

    Returns:
        ``{tool_name: kwargs}`` — the map consumed by
        ``registry.run_many_kwargs`` (shared ctx is merged by the registry).
    """
    addrs = extract_addresses(text)
    syms = symbols_in(text)
    chain = extract_chain(text)
    risk_rank = detect_risk_rank(text)

    per_tool: dict[str, dict[str, Any]] = {}
    for name in names:
        kwargs: dict[str, Any] = {}
        if name == "trending":
            # Chain-scoped: the user's requested chain filters the feed;
            # "all" when no chain was named (honest cross-chain label).
            kwargs["chain"] = chain or "all"
            kwargs["limit"] = 10
        if name in (
            "price_lookup",
            "scanner_intel",
            "security_scan",
            "token_meta",
            "top_traders",
            "wallet_analysis",
            "birdeye_market",
            "technical_analysis",
            "chart",
            "pattern_recall",
        ):
            kwargs["address"] = addrs[0] if addrs else ""
            kwargs["chain"] = chain
        # Address takes precedence; a $SYMBOL fills in when no address is given.
        if (
            name in ("price_lookup", "token_meta", "technical_analysis", "chart", "pattern_recall")
            and syms
            and not addrs
        ):
            kwargs["symbol"] = syms[0]
        if name == "trend_by_chain":
            kwargs["chain"] = chain or "all"
            kwargs["rank"] = "risk" if risk_rank else "trend"
        if name == "smart_money":
            kwargs["chain"] = chain or "solana"
        if name == "birdeye_market":
            kwargs["kind"] = "top_traders" if addrs else "smart_money"
            kwargs["chain"] = chain or "solana"
        if name == "web_search":
            kwargs["query"] = text
        per_tool[name] = kwargs
    return per_tool
