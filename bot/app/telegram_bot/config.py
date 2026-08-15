#!/usr/bin/env python3
"""
CryptoRugMunch Bot — Configuration
===================================
Tiers, channels, API endpoints, DEX ref links, and constants.
"""

import os

# ══════════════════════════════════════════════
# BOT
# ══════════════════════════════════════════════
BOT_TOKEN = os.getenv("RUGMUNCH_BOT_TOKEN", "")
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")
OWNER_IDS = {
    int(x.strip())
    for x in os.getenv("TELEGRAM_ALLOWED_USERS", "7075336557").split(",")
    if x.strip()
}
OWNER_USERNAME = "cryptorugmunch"  # Only this user gets admin access
BOT_USERNAME = "rugmunchbot"

# ══════════════════════════════════════════════
# BRANDING & SOCIAL
# ══════════════════════════════════════════════
WEBSITE_URL = "https://rugmunch.io"
WEB_APP_URL = f"{WEBSITE_URL}/scanner"
TWITTER_URL = "https://x.com/cryptorugmunch"
TELEGRAM_CHANNEL = "https://t.me/cryptorugmuncher"
TELEGRAM_GROUP = "https://t.me/crmunchers"
SUPPORT_EMAIL = "biz@rugmunch.io"
ENTERPRISE_CONTACT = "biz@rugmunch.io"

SOCIAL_LINKS = {
    "website": {"emoji": "🌐", "name": "RugMunch.io", "url": WEBSITE_URL},
    "twitter": {"emoji": "𝕏", "name": "Twitter/X", "url": TWITTER_URL},  # noqa: RUF001
    "channel": {"emoji": "📢", "name": "Alerts Channel", "url": TELEGRAM_CHANNEL},
    "group": {"emoji": "💬", "name": "Community Chat", "url": TELEGRAM_GROUP},
}

# Bot profile (set via BotFather API on startup)
BOT_DESCRIPTION = (
    "🛡️ Rug Muncher — The best crypto scam detective on Telegram. "
    "Scan any token across 77+ chains. Charts, bubble maps, deployer "
    "investigation, wallet forensics, and live trade feeds — all inline. "
    "AI-powered with RAG threat intel. Free tier available. "
    "Chat: t.me/crmunchers | Alerts: t.me/cryptorugmuncher"
)
BOT_SHORT_DESCRIPTION = "🛡️ AI Crypto Security — Token scanner, charts, bubble maps, deployer tracing. Better than every competitor. Free."

# ══════════════════════════════════════════════
# API ENDPOINTS
# ══════════════════════════════════════════════
BACKEND_URL = os.getenv("RMI_BACKEND_URL", "http://localhost:8000")
DATABUS_URL = f"{BACKEND_URL}/api/v1/databus"
RAG_URL = f"{BACKEND_URL}/api/v1/rag"
RAG_SEARCH_URL = f"{BACKEND_URL}/api/v1/rag/v2/search"
RAG_INGEST_URL = f"{BACKEND_URL}/api/v1/rag/v2/ingest"

# ══════════════════════════════════════════════
# RAG COLLECTIONS (crypto expert knowledge bases)
# ══════════════════════════════════════════════
RAG_COLLECTIONS = [
    "scam_intel",  # rug pulls, honeypots, scam patterns
    "wallet_labels",  # known scammer/exploiter wallets
    "contract_audit",  # smart contract vulnerability patterns
    "phishing_db",  # phishing sites and addresses
    "deployer_history",  # deployer behavior analysis
]
# Max daily AI questions for free users
FREE_AI_DAILY_LIMIT = int(os.getenv("FREE_AI_DAILY_LIMIT", "30"))

# Auth for backend calls (must match rmi-backend's RMI_AUTH_TOKEN)
RMI_AUTH_TOKEN = os.getenv("RMI_AUTH_TOKEN", "")

# ══════════════════════════════════════════════
# LOCAL LLAMA.CPP — Direct llama-server endpoints
# ══════════════════════════════════════════════
# llama.cpp servers run locally and provide OpenAI-compatible chat endpoints.
# Model groups:
#   - Qwen 3 4B on port 8085 → "qwen-chat" (fast, conversational)
#   - Qwen3.5-9B on port 8086 → "qwen-analysis" (deep reasoning)
# chat_template_kwargs.enable_thinking=false disables reasoning preamble.
LLAMA_HOST = os.getenv("LLAMA_HOST", "172.22.0.1")

# Legacy LiteLLM config (kept for backward compat, no longer used)
LITELLM_URL = os.getenv("LITELLM_URL", "http://127.0.0.1:4000/v1")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-litellm")
AI_CHAT_MODEL = os.getenv("AI_CHAT_MODEL", "chat")  # LiteLLM group → qwen-chat
AI_ANALYSIS_MODEL = os.getenv("AI_ANALYSIS_MODEL", "analysis")  # LiteLLM group → qwen-analysis

# Legacy direct llama.cpp backend (fallback when LiteLLM is unavailable)
LLAMA_HOST = os.getenv("LLAMA_HOST", "172.22.0.1")
LLM_CHAT_URL = os.getenv("LLM_CHAT_URL", "http://localhost:8093/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3-8b")
# Market rundown endpoint served by the same llama-server backend (chat mode).
MARKET_RUNDOWN_URL = os.getenv("MARKET_RUNDOWN_URL", "http://localhost:8095/chat/rundown")

# ══════════════════════════════════════════════
# V3 — SCANNER & SENTINEL ENDPOINTS
# ══════════════════════════════════════════════
# RugMunchScan v2 (synchronous full report) with v1 (async + poll) fallback.
SCANNER_V2_URL = os.getenv("SCANNER_V2_URL", f"{BACKEND_URL}/api/v2/scanner/token/scan")
SCANNER_V1_URL = os.getenv("SCANNER_V1_URL", f"{BACKEND_URL}/api/v1/scanner/scan")
# Cached scanner endpoints (instant from Redis — sub-second response):
SCANNER_CACHE_RISK_URL = os.getenv(
    "SCANNER_CACHE_RISK_URL", f"{BACKEND_URL}/api/v2/scanner/token/risk"
)  # → /{chain}/{address}
SCANNER_CACHE_FULL_URL = os.getenv(
    "SCANNER_CACHE_FULL_URL", f"{BACKEND_URL}/api/v2/scanner/token/full"
)  # → /{chain}/{address}
# SENTINEL deep forensic scan (premium — Scout+ or ⭐250).
SENTINEL_URL = os.getenv("SENTINEL_URL", f"{BACKEND_URL}/api/v1/x402-tools/sentinel_scan")

# RugCharts & RugMaps endpoints (public website API):
RUGCHARTS_TRENDING_URL = os.getenv(
    "RUGCHARTS_TRENDING_URL", f"{WEBSITE_URL}/api/v1/rugcharts/trending"
)
RUGCHARTS_OHLCV_URL = os.getenv(
    "RUGCHARTS_OHLCV_URL", f"{WEBSITE_URL}/api/v1/rugcharts/ohlcv"
)  # → /{chain}/{pair}
RUGMAPS_TOKEN_GRAPH_URL = os.getenv(
    "RUGMAPS_TOKEN_GRAPH_URL", f"{WEBSITE_URL}/api/v1/rugmaps/token-graph"
)

# ══════════════════════════════════════════════
# V3 — ALERT PIPELINE
# ══════════════════════════════════════════════
# Backend alert queue polled by the bot's alert delivery job (polling keeps the
# bot stateless; the design recommends webhook delivery as a later upgrade).
ALERT_POLL_URL = os.getenv("ALERT_POLL_URL", f"{BACKEND_URL}/api/v1/intelligence/my-alerts")
ALERT_POLL_INTERVAL = int(os.getenv("ALERT_POLL_INTERVAL", "30"))  # seconds
ALERT_MAX_PER_HOUR = int(os.getenv("ALERT_MAX_PER_HOUR", "5"))  # hard anti-spam cap
ALERT_DEDUPE_WINDOW = int(os.getenv("ALERT_DEDUPE_WINDOW", "1800"))  # 30 min per (token,type)

# ══════════════════════════════════════════════
# SUBSCRIPTION TIERS (Weekly Allotments)
# ══════════════════════════════════════════════
# Weekly model: resets every Monday 00:00 UTC.
# Prevents day-one dump abuse and smooths API load.
# Beta mode: Pro and Hunter are "Coming Soon" — gate with friendly messages.
TIERS = {
    "free": {"scans_per_week": 50, "ai_msgs_per_week": 30, "name": "🅱️ Free Beta"},
    "pro": {"scans_per_week": 150, "ai_msgs_per_week": 50, "name": "⭐ Pro (Coming Soon)"},
    "hunter": {"scans_per_week": 400, "ai_msgs_per_week": 100, "name": "🎯 Hunter (Coming Soon)"},
}

# Legacy tier details for informational display only (features list, etc.)
TIER_DETAILS = {
    "free": {
        "name": "Free Beta",
        "emoji": "🅱️",
        "scans_per_week": 50,
        "ai_msgs_per_week": 30,
        "price_monthly": 0,
        "features": [
            "🅱️ Unlimited access during beta",
            "50 scans/week (50/day effective)",
            "30 AI questions/week",
            "Basic risk score",
            "Contract safety check",
            "Liquidity overview",
            "Top 10 holders",
            "Community access",
        ],
    },
    "pro": {
        "name": "Pro",
        "emoji": "⭐",
        "scans_per_week": 150,
        "ai_msgs_per_week": 50,
        "price_monthly": 29.99,
        "price_stars": 1500,
        "features": [
            "150 scans/week",
            "50 AI questions/week",
            "Advanced risk scoring",
            "Bundle detection",
            "Fake volume detection",
            "Dev wallet finder",
            "Holder trend analysis",
            "Smart money alerts",
            "Watchlist (10 tokens)",
            "Export reports",
            "🅱️ Coming Soon — All features FREE during beta",
        ],
    },
    "hunter": {
        "name": "Hunter",
        "emoji": "🎯",
        "scans_per_week": 400,
        "ai_msgs_per_week": 100,
        "price_monthly": 49.99,
        "price_stars": 2500,
        "features": [
            "400 scans/week",
            "100 AI questions/week",
            "Everything in Pro, plus:",
            "Full wallet forensics",
            "First buyer analysis",
            "Exchange flow tracking",
            "Fresh wallet detection",
            "Shill detection",
            "Social sentiment scan",
            "Watchlist (50 tokens)",
            "Real-time alerts",
            "TA chart generation",
            "🅱️ Coming Soon — All features FREE during beta",
        ],
    },
}

# ── Beta mode gate messages ──
BETA_DEEP_SCAN_GATE = (
    "🅱️ <b>Free Beta — Unlimited Access!</b>\n\n"
    "During our beta period, all features including deep scan are unlocked "
    "for every user. Enjoy unlimited access while we build out premium tiers.\n\n"
    "⭐ <b>Pro tiers launching soon</b> — join our community to get early access!\n\n"
    "💬 Join: @crmunchers | 📢 Updates: @cryptorugmuncher"
)

BETA_ADVANCED_SCAN_GATE = (
    "🅱️ <b>Free Beta — Unlimited Access!</b>\n\n"
    "Advanced scanner features (transaction history, social sentiment, "
    "similar token analysis) are fully unlocked during beta.\n\n"
    "⭐ <b>Pro tiers launching soon</b> — premium features will remain free "
    "for early adopters who sign up at rugmunch.io!\n\n"
    "💬 @crmunchers | 📢 @cryptorugmuncher"
)

BETA_AI_GATE = (
    "🅱️ <b>Free Beta — Unlimited AI!</b>\n\n"
    "AI-powered analysis is fully unlocked during beta. "
    "Enjoy 30 AI messages/week — more than enough for power users.\n"
    "⭐ <b>Pro tiers launching soon</b> — sign up at rugmunch.io for early access.\n\n"
    "💬 @crmunchers | 📢 @cryptorugmuncher"
)

# ══════════════════════════════════════════════
# USER REFERRAL LINKS (Add your own!)
# ══════════════════════════════════════════════
# These are shown in scan reports and on the website.
USER_REF_LINKS = {
    # "binance": {"name": "Binance", "url": "https://www.binance.com/en/register?ref=YOUR_CODE", "emoji": "🟡"},
    # "bybit": {"name": "Bybit", "url": "https://www.bybit.com/invite?ref=YOUR_CODE", "emoji": "🟠"},
    # "okx": {"name": "OKX", "url": "https://www.okx.com/join/YOUR_CODE", "emoji": "⚫"},
    # "coinbase": {"name": "Coinbase", "url": "https://coinbase.com/join/YOUR_CODE", "emoji": "🔵"},
    # "kucoin": {"name": "KuCoin", "url": "https://www.kucoin.com/r/YOUR_CODE", "emoji": "🟢"},
    # "gate": {"name": "Gate.io", "url": "https://www.gate.io/ref/YOUR_CODE", "emoji": "🔷"},
    # "mexc": {"name": "MEXC", "url": "https://www.mexc.com/register?inviteCode=YOUR_CODE", "emoji": "🔶"},
    # "bitget": {"name": "Bitget", "url": "https://www.bitget.com/referral?clacCode=YOUR_CODE", "emoji": "🌊"},
}

# ══════════════════════════════════════════════
# TOP-UP PACKS (Buy extra when you run out)
# ══════════════════════════════════════════════
# One-time purchases via Telegram Stars. No expiry —
# top-up balance carries over forever until used.
TOP_UP_PACKS = {
    "scans_10": {
        "name": "10 Extra Scans",
        "emoji": "🔬",
        "type": "scans",
        "amount": 10,
        "stars": 75,
    },
    "scans_50": {
        "name": "50 Extra Scans",
        "emoji": "🔬",
        "type": "scans",
        "amount": 50,
        "stars": 300,
    },
    "scans_100": {
        "name": "100 Extra Scans",
        "emoji": "🔬",
        "type": "scans",
        "amount": 100,
        "stars": 500,
    },
    "ai_10": {
        "name": "10 Extra AI Messages",
        "emoji": "🤖",
        "type": "ai_msgs",
        "amount": 10,
        "stars": 100,
    },
    "ai_50": {
        "name": "50 Extra AI Messages",
        "emoji": "🤖",
        "type": "ai_msgs",
        "amount": 50,
        "stars": 400,
    },
    "ai_100": {
        "name": "100 Extra AI Messages",
        "emoji": "🤖",
        "type": "ai_msgs",
        "amount": 100,
        "stars": 700,
    },
}

# ══════════════════════════════════════════════
# SCAN PACKS (One-time deep analysis purchases)
# ══════════════════════════════════════════════
SCAN_PACKS = {
    "deep_scan": {
        "name": "🔬 Deep Token Scan",
        "description": "Complete forensic analysis of ONE token",
        "includes": [
            "Price history",
            "Holder bubble map",
            "Wallet clusters",
            "Scam indicators",
            "AI prediction",
            "Dev history",
        ],
        "stars": 250,
    },
    "wallet_forensic": {
        "name": "🕵️ Wallet Forensic Report",
        "description": "Full wallet investigation — P&L, connections, history",
        "includes": [
            "Transaction history",
            "Counterparty analysis",
            "P&L breakdown",
            "Bundle links",
            "Scam DB check",
        ],
        "stars": 400,
    },
    "bundle_investigation": {
        "name": "🕸️ Bundle Investigation",
        "description": "Full cluster analysis — find every connected wallet",
        "includes": [
            "Funding source map",
            "Timing correlation",
            "Wash trade detection",
            "Holder overlap",
            "Exit strategy clues",
        ],
        "stars": 500,
    },
}

# Deep scan unlock: one-time Stars cost when the user is not on a paid tier.
DEEP_SCAN_STARS_COST = SCAN_PACKS["deep_scan"]["stars"]

# ══════════════════════════════════════════════
# DEX REFERRAL LINKS
# ══════════════════════════════════════════════
# These generate revenue when users trade through our links.
# Format: {platform: {url_template, chains, emoji, description}}
DEX_REF_LINKS = {
    "gmgn": {
        "name": "GMGN.ai",
        "emoji": "📊",
        "url": "https://gmgn.ai/sol/token/{token}?ref=rugmunch",
        "chains": ["solana", "ethereum", "base", "bsc"],
        "description": "Trade on GMGN (40% ref rebate)",
    },
    "axiom": {
        "name": "Axiom",
        "emoji": "🔷",
        "url": "https://axiom.trade/@crmuncher",
        "chains": ["solana", "ethereum", "base"],
        "description": "Trade on Axiom (30% ref rebate)",
    },
    "odinbot": {
        "name": "OdinBot",
        "emoji": "⚡",
        "url": "https://app.odinbot.io/join?code=x5pyi0",
        "chains": ["solana"],
        "description": "Fast copy trading (40% lifetime)",
    },
    "padre": {
        "name": "Padre",
        "emoji": "⚔️",
        "url": "https://trade.padre.gg/rk/crm",
        "chains": ["solana", "ethereum", "base"],
        "description": "Trade on Padre (35% cashback)",
    },
    "telemetry": {
        "name": "Telemetry",
        "emoji": "📡",
        "url": "https://app.telemetry.io/@2dx3m",
        "chains": ["solana"],
        "description": "Trade on Telemetry",
    },
    "jupiter": {
        "name": "Jupiter",
        "emoji": "🪐",
        "url": "https://jup.ag/swap/SOL-{token}",
        "chains": ["solana"],
        "description": "Best Solana DEX aggregator (50bps fee)",
    },
    "hyperliquid": {
        "name": "Hyperliquid",
        "emoji": "🌀",
        "url": "https://app.hyperliquid.xyz/trade",
        "chains": ["solana"],
        "description": "Perps trading (50bps builder fee)",
    },
    "birdeye": {
        "name": "Birdeye",
        "emoji": "🦅",
        "url": "https://birdeye.so/token/{token}?ref=rugmunch",
        "chains": ["solana", "ethereum", "base", "bsc", "arbitrum"],
        "description": "Token analytics & charts",
    },
    "photon": {
        "name": "Photon",
        "emoji": "⚡",
        "url": "https://photon-sol.tinyastro.io/en/lp/{token}?handle=rugmunch",
        "chains": ["solana", "ethereum", "base"],
        "description": "Fast Solana trading terminal",
    },
    "bullx": {
        "name": "BullX",
        "emoji": "🐂",
        "url": "https://neo.bullx.io/terminal?chain={chain}&address={token}&ref=rugmunch",
        "chains": ["solana", "ethereum", "base", "bsc", "arbitrum"],
        "description": "Multi-chain trading bot",
    },
    "oneinch": {
        "name": "1inch",
        "emoji": "🦄",
        "url": "https://app.1inch.io/#/{chain_id}/simple/swap/ETH/{token}?ref=rugmunch",
        "chains": ["ethereum", "base", "arbitrum", "bsc", "polygon", "avalanche"],
        "description": "Best EVM DEX aggregator",
    },
    "pancakeswap": {
        "name": "PancakeSwap",
        "emoji": "🥞",
        "url": "https://pancakeswap.finance/swap?inputCurrency=BNB&outputCurrency={token}&ref=rugmunch",
        "chains": ["bsc", "ethereum", "arbitrum", "base"],
        "description": "BSC's #1 DEX",
    },
    "dexscreener": {
        "name": "DexScreener",
        "emoji": "📈",
        "url": "https://dexscreener.com/{chain}/{token}",
        "chains": [
            "solana",
            "ethereum",
            "base",
            "bsc",
            "arbitrum",
            "polygon",
            "avalanche",
        ],
        "description": "Real-time price charts",
    },
    "dextools": {
        "name": "DexTools",
        "emoji": "🔧",
        "url": "https://www.dextools.io/app/en/{chain}/pair-explorer/{token}",
        "chains": [
            "solana",
            "ethereum",
            "base",
            "bsc",
            "arbitrum",
            "polygon",
            "avalanche",
        ],
        "description": "DEX trading tools & charts",
    },
}

# Chain ID mapping for 1inch
CHAIN_IDS = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "avalanche": 43114,
    "optimism": 10,
    "base": 8453,
    "fantom": 250,
}

# ══════════════════════════════════════════════
# CHANNELS
# ══════════════════════════════════════════════
CHANNELS = {
    "main": {
        "id": "-1002056885429",
        "name": "RMI Community Hub",
        "handle": "@cryptorugmuncher",
    },
    "news": {
        "id": "-1003982391578",
        "name": "RMI Crypto News 24/7",
        "handle": "@rmicryptonews",
    },
    "alerts": {
        "id": "-1003818352164",
        "name": "RMI Crypto Rug Alerts",
        "handle": "@rmialerts",
    },
    "alpha": {
        "id": "-1003762675055",
        "name": "RMI Crypto Alpha Signals",
        "handle": "@rmialpha",
    },
    "scans": {
        "id": "-1003937506770",
        "name": "RMI Token Scanner",
        "handle": "@rmiscans",
    },
}

# ══════════════════════════════════════════════
# RISK VISUALIZATION
# ══════════════════════════════════════════════
RISK_COLORS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
    "safe": "✅",
    "unknown": "⚪",
}


def risk_bar(score: float, length: int = 10) -> str:
    """Generate a visual risk bar (0-100 scale)."""
    filled = int((score / 100) * length)
    if score >= 80:
        return "🔴" * filled + "⬜" * (length - filled) + f" {score}% DANGER"
    elif score >= 60:
        return "🟠" * filled + "⬜" * (length - filled) + f" {score}% RISKY"
    elif score >= 40:
        return "🟡" * filled + "⬜" * (length - filled) + f" {score}% CAUTION"
    elif score >= 20:
        return "🟢" * filled + "⬜" * (length - filled) + f" {score}% LOW"
    else:
        return "✅" * filled + "⬜" * (length - filled) + f" {score}% SAFE"


def threat_indicator(label: str, found: bool) -> str:
    return f"{'🔴' if found else '✅'} {label}"


# ══════════════════════════════════════════════
# FORMATTING HELPERS
# ══════════════════════════════════════════════
def fmt_number(n) -> str:
    """Format large numbers: 1.2M, 450K, etc."""
    if n is None:
        return "N/A"
    try:
        n = float(n)
    except (ValueError, TypeError):
        return str(n)
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    elif n >= 1e6:
        return f"${n / 1e6:.2f}M"
    elif n >= 1e3:
        return f"${n / 1e3:.1f}K"
    elif n >= 1:
        return f"${n:.4f}"
    elif n >= 0.01:
        return f"${n:.6f}"
    else:
        return f"${n:.10f}"


def fmt_pct(n) -> str:
    if n is None:
        return "N/A"
    try:
        return f"{float(n):.2f}%"
    except (ValueError, TypeError):
        return str(n)


def fmt_chain(chain: str) -> str:
    """Return chain emoji + name."""
    emojis = {
        "solana": "◎",
        "ethereum": "⟠",
        "bsc": "🔶",
        "polygon": "🟣",
        "arbitrum": "🔵",
        "optimism": "🔴",
        "base": "🔷",
        "avalanche": "🔺",
        "fantom": "👻",
        "tron": "🔻",
        "bitcoin": "₿",
    }
    return f"{emojis.get(chain.lower(), '⛓️')} {chain.capitalize()}"


# ══════════════════════════════════════════════
# SCAM SCHOOL TOPICS
# ══════════════════════════════════════════════
SCAM_SCHOOL_TOPICS = {
    "honeypot": {
        "title": "🍯 Honeypot Tokens",
        "content": "A honeypot lets you buy but prevents you from selling. The contract has hidden code that only allows whitelisted addresses to sell.\n\n🔍 *How to spot:*\n• Can buy but can't sell\n• Only 1-2 sellers ever\n• Contract has sell restrictions\n• Honeypot.is flags it\n\n🛡️ *Protection:* Always test with a small amount first, and use /scan before buying.",
    },
    "rugpull": {
        "title": "🏃 Rug Pulls",
        "content": "Developers drain all liquidity from a token, making it worthless. Can happen via:\n\n• Removing LP tokens\n• Minting infinite tokens\n• Hidden sell functions\n• Blacklisting buyers\n\n🔍 *How to spot:*\n• Unlocked liquidity\n• Unrenounced contract\n• Dev holds >20% supply\n• New wallet, no history\n\n🛡️ *Protection:* Check /scan for LP lock status and dev wallet analysis.",
    },
    "bundling": {
        "title": "📦 Bundle Scams",
        "content": "Devs use multiple wallets to create fake organic activity. They buy from 10-50 wallets funded from the same source.\n\n🔍 *How to spot:*\n• Multiple wallets funded from same CEX/bridge\n• Identical buy amounts\n• Same-timestamp transactions\n• Wallets only hold this one token\n\n🛡️ *Protection:* Use /scan bundle detection to trace funding sources.",
    },
    "wash_trading": {
        "title": "🔄 Wash Trading",
        "content": "Artificially inflating volume by buying and selling between own wallets.\n\n🔍 *How to spot:*\n• High volume, low unique traders\n• Same wallets trading back and forth\n• Round number trade amounts\n• Volume doesn't match market cap\n\n🛡️ *Protection:* /scan checks volume authenticity.",
    },
    "fresh_wallets": {
        "title": "🆕 Fresh Wallet Red Flags",
        "content": "When most holders are brand new wallets (created <7 days ago), it often means the dev created fake holders.\n\n🔍 *How to spot:*\n• >50% wallets <7 days old\n• Wallets only hold 1 token\n• No transaction history before buying\n• Similar naming patterns\n\n🛡️ *Protection:* /scan shows wallet age distribution.",
    },
    "exchange_flow": {
        "title": "🏦 Exchange Flow Analysis",
        "content": "Tracking when large holders move tokens to exchanges signals potential dumps.\n\n🔍 *How to spot:*\n• Top holders sending to CEX wallets\n• Increasing exchange deposits\n• Unusual transfer patterns\n• Pre-scheduled dumps\n\n🛡️ *Protection:* Hunter+ tiers get real-time exchange flow alerts.",
    },
}
