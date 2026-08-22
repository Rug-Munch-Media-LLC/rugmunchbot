"""Intent keyword tables, chain aliases, concept->tool mapping."""

from __future__ import annotations

import re

_TRENDING_WORDS = re.compile(
    r"\b(trending|hot|pump|pumping|movers|gainers|top tokens?|what'?s? (hot|pumping|moving)|new launches|launching)\b",
    re.IGNORECASE,
)


_PRICE_WORDS = re.compile(
    r"\b(price|worth|value|cost|how much|chart|pump|dump|market cap|mcap|fdv|trading at)\b",
    re.IGNORECASE,
)


_RISK_RANK_WORDS = re.compile(
    r"\b(riskiest|riskier|risky|sketchiest|sketchy|shadiest|shady|"
    r"scammy|scammiest|suspicious|sus|worst|most dangerous|dangerous|"
    r"most likely to (rug|scam|dump)|avoid|red flags?|rug risk|"
    r"honeypot risk|survive( the )?day|dump candidates?)\b",
    re.IGNORECASE,
)


_PICKS_WORDS = re.compile(r"\b(picks?|tokens?|coins?)\b", re.IGNORECASE)


_TA_WORDS = re.compile(
    r"\b(technical analysis|technical indicators?|the ta|ta (for|on|of)|"
    r"rsi|macd|ema|candlestick|chart patterns?|support and resistance|support/resistance)\b",
    re.IGNORECASE,
)


_PREDICT_TA_WORDS = re.compile(
    r"\b(will (it|this|that|(this|that) (token|coin|one)|\$?[a-z0-9]+) (pump|dump|go up|go down|rise|rally|moon|crash|surge|fall)|"
    r"is ((it|this|that)( token| coin)?|\$?[a-z0-9]+) (going to|about to) (pump|dump|moon|crash)|"
    r"pump or dump|gonna (pump|dump|moon)|"
    r"(price )?(prediction|forecast) (for|on)|"
    r"what('s| is) the (prediction|outlook|forecast)( for)?|"
    r"where('s| is) (it|this|that|\$?[a-z0-9]+) (heading|going)|"
    r"price (target|prediction)|will it (keep|continue) (rising|pumping))\b",
    re.IGNORECASE,
)


_CHART_WORDS = re.compile(
    r"\b(price chart|candlestick chart|candle chart|ascii chart|sparkline|"
    r"chart (please|for|of|on|showing|say|says|look|looks|tell|show)|"
    r"show me (a |the )?chart|draw (a |the )?chart|chart of|charts for|"
    r"chart\s+\$[A-Za-z]{2,10})|\$[A-Za-z]{2,10}\s+chart\b",
    re.IGNORECASE,
)


_PATTERN_WORDS = re.compile(
    r"\b(have we seen (this|that) (pattern|before)|seen (this|that) pattern before|"
    r"seen this before|similar patterns?|pattern memory|"
    r"seen (a|this) (pump-and-dump|honeypot|rug|pump|dump|pattern) before)\b",
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


_CONCEPT_WORD_TOOLS: dict[str, tuple[str, ...]] = {
    "trending": ("trending",),
    "market": ("market_overview", "market_condition"),
    "sentiment": ("market_condition",),
    "price": ("price_lookup",),
    "chart": ("chart",),
    "technical": ("technical_analysis",),
    "pattern": ("pattern_recall",),
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
