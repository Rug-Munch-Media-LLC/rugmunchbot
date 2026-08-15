"""Intent routing — decide which tools to run for a user question.

Deterministic, cheap, and fast (no LLM round-trip for routing): keyword +
pattern matching on the user's question. The tools themselves always return
REAL data; the router only decides WHICH tools are relevant.

Intents map to tools:
  * trending / hot / what's pumping    -> trending (+ news_headlines, + trend_by_chain when a chain is named)
  * $SYMBOL / price of X               -> price_lookup
  * TA / technical analysis / rsi / macd on X -> technical_analysis (+ price_lookup)
  * who's buying / smart money / top traders -> top_traders (needs address), smart_money, birdeye_market
  * scan / risk / honeypot + address   -> scanner_intel, security_scan, token_meta (image)
  * wallet / holdings / pnl + address  -> wallet_analysis
  * prediction markets / polymarket / odds on X -> prediction_markets
  * market overview / fear and greed / btc price -> market_overview (+ price_lookup)
  * how's the market / sentiment / bullish-bearish / risk on-off / good time to buy
                                        -> market_condition (synthesized read), market_overview for plain "market overview"
  * news / market context / general    -> web_search, trending, news_headlines

``route_tools`` decides WHICH tools run; ``tool_kwargs`` maps each selected
tool to the kwargs it understands (address / symbol / chain / kind). Both are
pure and deterministic — no LLM round-trip, no symbol synthesis.

Intent understanding helpers (all deterministic, all unit-tested):
  * ``detect_negations`` — which intent concepts the question negates
    ("what's NOT trending?" -> {"trending"}). Pure negation does NOT change
    routing (the tool still runs so the answer is grounded in real data).
  * ``detect_exclusions`` — which concepts the user explicitly told us to skip
    ("don't show me trending" -> {"trending"}). Exclusions DO suppress their
    tools so we never burn a call on data the user said not to show.
  * ``detect_comparison`` / ``compare_pairs`` — "compare $BONK vs $WIF"
    (extracts the ($BONK, $WIF) pair; routing keeps price_lookup).
  * multi-intent — a single question may route several tools; order is stable
    and the caller may run them concurrently.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

from rugmunchbot.tools.classify import (
    SYMBOL_RE,
    extract_addresses,
    has_symbol,
    symbols_in,
)

_TRENDING_WORDS = re.compile(
    r"\b(trending|hot|pump|pumping|movers|gainers|top tokens?|what'?s? (hot|pumping|moving)|new launches|launching)\b",
    re.IGNORECASE,
)
_PRICE_WORDS = re.compile(
    r"\b(price|worth|value|cost|how much|chart|pump|dump|market cap|mcap|fdv|trading at)\b",
    re.IGNORECASE,
)
# Technical-analysis questions — route to the technical_analysis tool (real
# RSI-14 / EMA-9/21 / MACD / support-resistance, see tools/ta.py). "ta"
# alone is risky in prose, so the bare token must be followed by for/on/of;
# the full phrases and indicator names always match.
_TA_WORDS = re.compile(
    r"\b(technical analysis|technical indicators?|the ta|ta (for|on|of)|"
    r"rsi|macd|ema|candlestick|chart patterns?|support and resistance|support/resistance)\b",
    re.IGNORECASE,
)
_SMART_MONEY_WORDS = re.compile(
    r"\b(smart money|smart-money|who'?s? buying|who is buying|buying|buyers|top traders?|top holders?|whales?|accumulating|early buyers)\b",
    re.IGNORECASE,
)
_SECURITY_WORDS = re.compile(
    r"\b(scan|audit|honeypot|rug|risk|safe|scam|security|malicious|token check|is it a)\b",
    re.IGNORECASE,
)
_WALLET_WORDS = re.compile(
    r"\b(wallet|holdings|portfolio|pnl|profit|loss|balance|tokens in)\b",
    re.IGNORECASE,
)
_NEWS_WORDS = re.compile(
    r"\b(news|headlines|market (today|now)|context|what'?s? happening|update|latest)\b",
    re.IGNORECASE,
)
_PREDICTION_WORDS = re.compile(
    r"\b(prediction markets?|polymarket|kalshi|odds (on|for|of)|who will win|chances of|probability of)\b",
    re.IGNORECASE,
)
_MARKET_OVERVIEW_WORDS = re.compile(
    r"\b(market overview|how('s| is| are)? (the )?market|crypto market|fear and greed|"
    r"market (status|update|today)|btc price|bitcoin price|eth price|sol price|"
    r"price of (btc|bitcoin|eth|sol|solana))\b",
    re.IGNORECASE,
)

# Market-CONDITION questions — the synthesized read ("how's the market NOW").
# Deliberately does NOT include "market overview" (that stays with
# market_overview so the exact-match routing contract is preserved).
_MARKET_CONDITION_WORDS = re.compile(
    r"\b(market sentiment|sentiment (of|in|on) the market|what'?s? the sentiment|"
    r"how('s| is| are)? (the )?(crypto )?market|how are (the )?markets?|"
    r"(is )?the market (is )?(not )?(bullish|bearish)|market (bullish|bearish|sentiment|condition|read)|"
    r"(bullish|bearish) (right now|today|overall)|(bullish|bearish) or (bullish|bearish)|"
    r"risk-on|risk on|risk-off|risk off|risk appetite|"
    r"good time to (buy|enter|ape|jump in)|is (it|now) a good time|time to (buy|enter)|"
    r"should i (buy|enter|ape|jump in)|worth buying|buy the dip)\b",
    re.IGNORECASE,
)

# Chain-name extraction: canonical aliases -> DexScreener-style chain id.
# Mirrors trend_algo._CHAIN_ALIASES (keep in sync). Word-boundary anchored so
# "op" never matches inside "top" or "polymarket".
_CHAIN_ALIASES: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "solana": "solana",
    "sol": "solana",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "binance-smart-chain": "bsc",
    "binance": "bsc",
    "bsc": "bsc",
    "bnb": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "optimism": "optimism",
    "op": "optimism",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "berachain": "berachain",
    "bera": "berachain",
    "hyperliquid": "hyperliquid",
    "zksync": "zksync",
    "robinhood": "robinhood",
    "injective": "injective",
    "fantom": "fantom",
    "ftm": "fantom",
    "linea": "linea",
    "blast": "blast",
    "sonic": "sonic",
    "scroll": "scroll",
    "mantle": "mantle",
    "tron": "tron",
    "trx": "tron",
    "ton": "ton",
    "base": "base",
    "sei": "sei",
    "sui": "sui",
    "aptos": "aptos",
    "mode": "mode",
}

_CHAIN_RE = re.compile(
    r"\b(" + "|".join(sorted(_CHAIN_ALIASES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


# ── Negation / exclusion / comparison understanding ────────────────────────
# Canonical intent concepts -> the tools that answer them (used by the
# exclusion machinery so "don't show me X" suppresses exactly X's tools).
_CONCEPT_WORD_TOOLS: dict[str, tuple[str, ...]] = {
    "trending": ("trending",),
    "market": ("market_overview", "market_condition"),
    "sentiment": ("market_condition",),
    "price": ("price_lookup",),
    "technical": ("technical_analysis",),
    "security": ("scanner_intel", "security_scan", "token_meta"),
    "wallet": ("wallet_analysis",),
    "news": ("news_headlines", "web_search"),
    "prediction": ("prediction_markets",),
}

_NEGATION_WORDS = {
    "not",
    "isn't",
    "aren't",
    "wasn't",
    "weren't",
    "don't",
    "doesn't",
    "didn't",
    "won't",
    "never",
    "no",
}

_EXCLUSION_MARKER_RE = re.compile(
    r"\b(without|except|excluding|skip|never mind|drop the|not interested in|"
    r"don'?t (show|want|need|include)|stop (showing|including))\b",
    re.IGNORECASE,
)

_COMPARE_RE = re.compile(
    r"\b(compare|vs\.?|versus|which is better|which should i (pick|buy|choose))\b",
    re.IGNORECASE,
)


def _concept_for_word(word: str) -> str:
    """Map a single token to its intent concept ('' when none)."""
    w = word.strip().lower()
    if w in {"trending", "trend", "hot", "pumping", "movers", "gainers"}:
        return "trending"
    if w in {"market", "markets"}:
        return "market"
    if w in {"sentiment", "bullish", "bearish", "condition", "risk-on", "risk-off"}:
        return "sentiment"
    if w in {"price", "prices", "chart", "mcap", "worth", "value"}:
        return "price"
    if w in {"ta", "technical", "indicators", "rsi", "macd", "ema"}:
        return "technical"
    if w in {"honeypot", "rug", "risk", "risky", "safe", "security", "scam", "audit", "scan"}:
        return "security"
    if w in {"wallet", "holdings", "portfolio", "pnl", "balance"}:
        return "wallet"
    if w in {"news", "headlines", "context"}:
        return "news"
    if w in {"prediction", "odds", "polymarket", "kalshi"}:
        return "prediction"
    return ""


def detect_negations(text: str) -> set[str]:
    """Canonical intent concepts the question NEGATES (metadata, not routing).

    E.g. ``"what's NOT trending?"`` -> ``{"trending"}``;
    ``"is $BONK not a honeypot?"`` -> ``{"security"}``.

    Pure negation does NOT suppress a tool — the tool still runs so the
    answer can be grounded in real data (the LLM handles the negative
    construction). Request-level EXCLUSIONS ("don't show me X") are handled
    separately by :func:`detect_exclusions` and DO suppress routing.
    """
    tokens = re.findall(r"[a-z0-9']+", text.lower())
    concepts: set[str] = set()
    for i, tok in enumerate(tokens):
        if tok not in _NEGATION_WORDS:
            continue
        for j in range(i + 1, min(i + 3, len(tokens))):
            concept = _concept_for_word(tokens[j])
            if concept:
                concepts.add(concept)
    return concepts


def detect_exclusions(text: str) -> set[str]:
    """Concepts the user explicitly asked to EXCLUDE from the answer.

    E.g. ``"don't show me trending"`` -> ``{"trending"}``;
    ``"without the market stuff"`` -> ``{"market"}``;
    ``"except the news"`` -> ``{"news"}``.

    These concepts' tools are NOT routed (the user said not to show them) —
    see :func:`route_tools`. Conservative: only the FIRST concept after an
    exclusion marker is treated as excluded, so a trailing positive ask
    ("don't show me trending, give me prices") is never over-suppressed.
    """
    out: set[str] = set()
    for m in _EXCLUSION_MARKER_RE.finditer(text):
        tail = text[m.end() : m.end() + 80].lower()
        for word in re.findall(r"[a-z0-9']+", tail):
            concept = _concept_for_word(word)
            if concept:
                out.add(concept)
                break
    return out


def _excluded_tools(text: str) -> set[str]:
    """Tool names suppressed by explicit user exclusions."""
    excluded: set[str] = set()
    for concept in detect_exclusions(text):
        excluded.update(_CONCEPT_WORD_TOOLS.get(concept, ()))
    return excluded


def detect_comparison(text: str) -> bool:
    """True when the question explicitly asks to compare two tokens."""
    return bool(_COMPARE_RE.search(text))


def compare_pairs(text: str) -> list[tuple[str, str]]:
    """Extract ``($LEFT, $RIGHT)`` symbol pairs around a comparison connector.

    ``"compare $BONK vs $WIF"`` -> ``[("BONK", "WIF")]``. Only real
    ``$SYMBOL`` mentions are used — never synthesized from prose.
    """
    syms = [(m.start(), m.group(1).upper()) for m in SYMBOL_RE.finditer(text)]
    out: list[tuple[str, str]] = []
    for m in _COMPARE_RE.finditer(text):
        before = [s for pos, s in syms if pos < m.start()]
        after = [s for pos, s in syms if pos > m.end()]
        if before and after:
            out.append((before[-1], after[0]))
    return out


def extract_chain(text: str) -> str:
    """First canonical chain name mentioned in the question ('' if none).

    Returns the canonical DexScreener-style chain id ("solana", "ethereum",
    "base", "bsc", ...) or '' when no chain alias is present. Pure regex —
    never infers a chain from an address prefix.
    """
    m = _CHAIN_RE.search(text)
    if not m:
        return ""
    return _CHAIN_ALIASES[m.group(1).lower()]


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

    # Trending: cross-chain feed; when a chain is named also run the
    # per-chain trend_algo (transparent scoring over our data).
    if _TRENDING_WORDS.search(text) and "trending" not in excluded:
        tools.append("trending")
        if _NEWS_WORDS.search(text) and "news_headlines" not in excluded:
            tools.append("news_headlines")
        if chain:
            tools.append("trend_by_chain")

    if (has_ticker or _PRICE_WORDS.search(text)) and "price_lookup" not in excluded:
        tools.append("price_lookup")

    # Technical analysis: "what's the TA for $BONK" / "ta for $BONK" /
    # "technical analysis" / "rsi of btc". Routed on the phrase alone (like
    # price_lookup); without a symbol/address the tool degrades to ok=False
    # and the chat layer answers "no live data" — never a guess.
    if _TA_WORDS.search(text) and "technical_analysis" not in excluded:
        tools.append("technical_analysis")

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
    if (
        addrs
        and "security_scan" not in tools
        and "wallet_analysis" not in tools
        and "security_scan" not in excluded
    ):
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

    per_tool: dict[str, dict[str, Any]] = {}
    for name in names:
        kwargs: dict[str, Any] = {}
        if name in (
            "price_lookup",
            "scanner_intel",
            "security_scan",
            "token_meta",
            "top_traders",
            "wallet_analysis",
            "birdeye_market",
            "technical_analysis",
        ):
            kwargs["address"] = addrs[0] if addrs else ""
            kwargs["chain"] = chain
        # Address takes precedence; a $SYMBOL fills in when no address is given.
        if name in ("price_lookup", "token_meta", "technical_analysis") and syms and not addrs:
            kwargs["symbol"] = syms[0]
        if name == "trend_by_chain":
            kwargs["chain"] = chain or "all"
        if name == "smart_money":
            kwargs["chain"] = chain or "solana"
        if name == "birdeye_market":
            kwargs["kind"] = "top_traders" if addrs else "smart_money"
            kwargs["chain"] = chain or "solana"
        if name == "web_search":
            kwargs["query"] = text
        per_tool[name] = kwargs
    return per_tool


def build_tool_prompt_block(results: Mapping[str, object]) -> str:
    """Render tool results as a compact LIVE DATA block for the LLM prompt.

    Only REAL tool data is included (ok=True). The block labels the source
    so the model attributes and never presents tool data as its own memory.
    ``Mapping`` (covariant) so callers can pass ``dict[str, ToolResult]``.
    """
    lines: list[str] = ["LIVE MARKET DATA (fetched just now from real APIs):"]
    for name in sorted(results):
        result = results[name]
        if getattr(result, "ok", False) is False:
            continue
        from rugmunchbot.tools.registry import ToolResult

        if not isinstance(result, ToolResult):
            continue
        source = result.source or name
        lines.append(f"- {name} [source: {source}]:")
        payload = result.data
        if isinstance(payload, dict):
            for k, v in payload.items():
                text = str(v) if not isinstance(v, (dict, list)) else str(v)[:500]
                lines.append(f"    {k}: {text[:500]}")
        else:
            lines.append(f"    {str(payload)[:800]}")
    lines.append(
        "You may ONLY cite symbols, addresses, prices, and metrics that appear in this LIVE MARKET DATA. If the user asks for data not present here, say you don't have live data for it."
    )
    return "\n".join(lines)
