"""Telegram-news source config — env-overridable channel list + keywords."""

from __future__ import annotations

import os

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.tg_news")


_DEFAULT_CHANNELS: tuple[str, ...] = (
    # ── News outlets (View = t.me preview accessible) ──────────────────────────
    "cointelegraph",  # CoinTelegraph (View)
    "coindesk",  # CoinDesk (View)
    "cryptoslate",  # CryptoSlate (Contact)
    "WatcherGuru",  # Watcher Guru (View) — high-frequency news aggregator
    "bitcoinmagazine",  # Bitcoin Magazine (Contact)
    "PANewsOfficial",  # PANews — KR/CN → runtime language filter
    "cryptopotato",  # CryptoPotato (View)
    "coingape",  # CoinGape (View)
    "U_Today",  # U.Today (View)
    "newsbtc",  # NewsBTC (View)
    "dailyhodl",  # The Daily Hodl (View)
    "zycrypto",  # ZyCrypto (View)
    "coinpedia",  # Coinpedia (View)
    "blockworks",  # Blockworks (View)
    "cryptobriefing",  # Crypto Briefing (View)
    "coinjournal",  # CoinJournal (View)
    "thedefiant",  # The Defiant (Contact)
    "decryptmedia",  # Decrypt (Contact)
    "ambcrypto",  # AMBCrypto (Contact)
    "bitcoinist",  # Bitcoinist (Contact)
    "trustnodes",  # Trustnodes (Contact)
    "thebitcoinnews",  # The Bitcoin News (View)
    "altcoinbuzz",  # Altcoin Buzz — news/alpha (View)
    "cryptopanic",  # CryptoPanic — news aggregator (Contact)
    "cryptonewscom",  # Cryptonews.com (Contact)
    # ── News outlets — expansion batch (2026-08-18) ────────────────────────────
    "CryptoNewsDose",  # Crypto News Dose — daily roundup (View)
    "AltcoinToday",  # Altcoin Today — news/altcoins (View)
    "CoinMarketCap",  # CoinMarketCap — official (View)
    "CoinGecko",  # CoinGecko — official announcements (View)
    "binance_news",  # Binance News — official (View)
    "kucoinnews",  # KuCoin News — official (View)
    "bybit_announcements",  # Bybit — official (View)
    "cryptocomnews",  # Crypto.com — official news (View)
    "okx_announcements",  # OKX — official (View)
    "tokeninsight",  # TokenInsight — research/ratings (View)
    "cryptorank_io",  # CryptoRank — data/rankings (View)
    "coincodex",  # CoinCodex — market data (View)
    "Cryptonews",  # Cryptonews.com (View)
    "news_btc",  # NewsBTC alt (View)
    "blockchainnews",  # Blockchain News (View)
    "CryptoDaily",  # Crypto Daily — news/analysis (View)
    "cryptonewswire",  # Crypto News Wire — press releases (View)
    # ── Security / exploit alerts ──────────────────────────────────────────────
    "CertiKAlert",  # CertiK Alert (View)
    "PeckShieldAlert",  # PeckShield Alert (View)
    "rektnews",  # REKT News — exploit/incident intel (View)
    "ScamSniffer",  # ScamSniffer — phishing alerts (View)
    "zachxbt",  # ZachXBT — on-chain sleuth (View)
    "SlowMist",  # SlowMist — security team (Contact)
    "BlockSecTeam",  # BlockSec — security alerts (View)
    "halborn",  # Halborn — security audits/alerts (View)
    "solidityfinance",  # Solidity Finance — audits + alerts (View)
    "Hacken",  # Hacken — security audits (View)
    "verichains",  # Verichains — security research (View)
    "trmlabs",  # TRM Labs — crypto risk/security (View)
    "EthereumSecurity",  # Ethereum Foundation security (Contact)
    "FuzzLandAlert",  # FuzzLand — exploit/rugpull alerts (Contact)
    "BeosinAlert",  # Beosin — security alerts (Contact)
    "hypernative",  # Hypernative — exploit monitoring (Contact)
    "openzeppelin",  # OpenZeppelin — security research (Contact)
    "Cyvers",  # Cyvers — real-time security alerts (View)
    "AnciliaInc",  # Ancilia — security audits (View)
    "NeptuneMutual",  # Neptune Mutual — cover/security (View)
    "immunefi",  # Immunefi — bug bounty/alerts (View)
    "hackenio",  # Hacken — additional alerts (View)
    # ── On-chain / alpha / whale tracking ──────────────────────────────────────
    "lookonchain",  # Lookonchain — on-chain analytics (View)
    "spotonchain",  # Spot On Chain — on-chain alerts (View)
    "whale_alert",  # Whale Alert — large-transaction alerts (View)
    "ArkhamIntel",  # Arkham — entity/alert intel (Contact)
    "OnChainWizard",  # OnChain Wizard — alpha (Contact)
    "AltcoinDaily",  # Altcoin Daily — alpha/community (Contact)
    "glassnode",  # Glassnode — on-chain analytics (View)
    "cryptowhaletrades",  # Crypto Whale Trading — whale/on-chain (View)
    "nansen_ai",  # Nansen — on-chain analytics (Contact)
    "chainfeeds",  # ChainFeeds — crypto research digest (Contact)
    "intotheblock",  # IntoTheBlock — on-chain metrics (View)
    "SantimentFeed",  # Santiment — crypto data/insights (View)
    "CoinGlass",  # CoinGlass — liquidation data (View)
    "Coinalyze",  # Coinalyze — funding/volume (View)
    "duneanalytics",  # Dune — on-chain dashboards (View)
    "defillama",  # DeFi Llama — TVL/data (View)
    "MessariCrypto",  # Messari — research/intel (View)
    "tokenterminal",  # Token Terminal — fundamentals (View)
    "NerdStaking",  # Nerd Staking — validator/defi (View)
    # ── DeFi / protocol channels ───────────────────────────────────────────────
    "arbitrum",  # Arbitrum Official (View)
    "avax",  # Avalanche Official (View)
    "zksync",  # ZKsync Official (View)
    "starknet",  # Starknet (View)
    "aptos",  # Aptos Official (View)
    "lidofinance",  # Lido Finance (View)
    "chainlink",  # Chainlink Official (View)
    "Uniswap",  # Uniswap Official (Contact)
    "Aave",  # Aave Official (Contact)
    "base",  # Base Official (Contact)
    "solana",  # Solana Official (Contact)
    "ethereum",  # Ethereum Foundation (View)
    "optimism",  # Optimism Official (View)
    "polygon",  # Polygon Official (View)
    "bnbchain",  # BNB Chain Official (View)
    "sui",  # Sui Network (View)
    "NEARProtocol",  # NEAR Protocol (View)
    "cosmos",  # Cosmos — Interchain (View)
    "polkadot",  # Polkadot Official (View)
    "celestia",  # Celestia — modular (View)
    "avalancheavax",  # Avalanche official alt (View)
    "jupiterexchange",  # Jupiter — Solana DEX (View)
    "raydiumprotocol",  # Raydium — Solana AMM (View)
    "pancakeswap",  # PancakeSwap — BSC DEX (View)
    "aavegrants",  # Aave Grants (View)
    "Compound",  # Compound Finance (View)
    "MakerDAO",  # MakerDAO Official (View)
    "CurveFinance",  # Curve Finance (View)
    "balancer",  # Balancer — DEX (View)
    "yearnfinance",  # Yearn Finance (View)
    "convexfinance",  # Convex Finance (View)
    # ── Research & macro ───────────────────────────────────────────────────────
    "DelphiDigital",  # Delphi Digital — research (View)
    "alphanonce",  # Alpha Nome — research (View)
    # 2026-08-25 pruned: Telegram cannot resolve these usernames ("No user has
    # X as username" / "Nobody is using this username") — channel deleted or
    # renamed. Auto-disable cooldown persistence now also covers runtime-
    # discovered dead channels (e.g. wolfscryptocallz, dioralpha, sugarcalls,
    # wulfcalls, yennidegen) without config edits.
    # "ASXNResearch",  # ASXN — crypto research (View) — DEAD 2026-08-25
    "kryptocast",  # KryptoCast — interviews/analysis (View)
    "BanklessHQ",  # Bankless — media/analysis (View)
    "TheDeFiReport",  # DeFi Report — analysis (View)
    "CryptoCred",  # CryptoCred — trading/research (View)
    # "DoseofDeFi",  # Dose of DeFi — newsletter (View) — DEAD 2026-08-25
    # "milkroad",  # Milk Road — daily newsletter (View) — DEAD 2026-08-25
    # "defiedge",  # DeFi Edge — alpha/education (View) — DEAD 2026-08-25
    # ── Trading & market analysis ─────────────────────────────────────────────
    "CryptoBubbles",  # Crypto Bubbles — market heatmap (View)
    # "Coin360",  # Coin360 — market visualization (View) — DEAD 2026-08-25
    "MaterialIndicators",  # Material Indicators — trading (View)
    # "TheNews_Trader",  # TheNews.Trader — trade signals (View) — DEAD 2026-08-25
    # "AltcoinTrader",  # Altcoin Trader — signals/analysis (View) — DEAD 2026-08-25
    "TradingShot",  # Trading Shot — TA signals (View)
    "crypto_traders",  # Crypto Traders — community (View)
    "fibonacci_trading",  # Fibonacci Trading — TA (View)
    # ── News from X/Twitter personalities (cross-post channels) ───────────────
    # "wublockchain",  # Wu Blockchain — Colin Wu news — DEAD 2026-08-25
    # "Cointelegraph_",  # CoinTelegraph alt (View) — DEAD 2026-08-25
    "u_today",  # U.Today alt (View)
    # ── Verified from @cryptorugmunch account's follows (2026-08-18 live probe) ──
    # Major news with verified live channels
    "BlockchainReporter",  # Blockchain Reporter (View, 49k members)
    "CryptoWorldNews",  # CryptoWorldNews (View, 43k members)
    "Cryptocurrency_Inside",  # Crypto Bitcoin Ethereum News (View, 45k members)
    "cryptothedoggy",  # CryptoTheDoggy Blockchain News (View, 148k members)
    "FinancialWorldUpdates",  # Financial World Updates (View, 24k members)
    "danny_crypton",  # Danny Crypton (View, 43k members)
    # Major alpha/research channels (for news feed — posts have real news value)
    "CryptoCapoTG",  # CryptoCapo TG (View, 117k — news+analysis)
    "shahlito",  # Shah's Journal (View, 32k — crypto research)
    "spidersjournal",  # SpiderCrypto Trading Journal (View, 33k)
    # Security/exploit channels (verified live)
    "Rektproof",  # RektProof PA (View, 43k — security/PA)
    "thepumpalert",  # THE PUMP ALERT (View, 374 members — scam call-out channel)
    # On-chain / analytics (verified live, separate from the alpha call channels)
    "nansen_digest",  # Nansen Digest (View, 6.5k)
    "CryptoAlche",  # Crypto Alchemy (View, 10k+ — crypto analysis)
    "CallAnalyser",  # Call Analyser (View, 49k — KOL call analysis)
    "walloftrophies",  # KOL Trending / CallAnalyser (View, 15k)
    "skeletonlounge",  # Skeleton Lounge Portal (View, 23k)
    "rickburpbotnews",  # Rick's Lab (View, 70k — research/alpha)
    # ── Batch 2: more verified news channels from live probe ──
    "blockchainmedia",  # Blockchainmedia.id (View, 2k — crypto news)
    "Coinspeaker",  # Coinspeaker (View, 2k — crypto news)
    "coingeckonews",  # CoinGecko News (View, 10k — exchange news)
    "CryptoNobler",  # 0xNobler (View, 51k — crypto analysis)
    "dimethyeth",  # dime (View, 6k — crypto research)
    "kamocalls",  # Kamo | Daily trades (View, 3k — market analysis)
    "fullfeed",  # Drago Den Crypto Technical Signals (View, 7k — TA)
    "icodrops",  # ICO Drops (View, 43k — ICO/launch news)
    "BTC_Tick",  # Bitcoin Price (View, 4k — BTC price feed)
    "BTCPriceX",  # BTC Price (View, 11k — BTC price feed)
    "solusdprices",  # SOL Price (View, 13k — SOL price feed)
    "khmertimesbreakingnews",  # Khmer Times Breaking News (View, 21k — regional)
    # ── Known-dead handles (2026-08-16 MTProto probe: "No user has X as username") ──
    # "TheBlock__",  # The Block — DEAD handle
    # "beincrypto",  # BeInCrypto — DEAD handle
    # "cryptonews",  # Cryptonews.com — DEAD handle (use cryptonewscom instead)
)


#: Analyst / on-chain intelligence channels (quality overhaul 2026-08-29).
#: LookOnChain and friends produce high-signal on-chain analytics that the
#: channel doctrine routes to MAIN when importance >= the main gate (0.7)
#: and to the news page (website feed) always — citing the source link.
#: These handles are force-swept as ``crypto_news`` with
#: ``channel_category="analyst"`` so auto-discovery can never drop them
#: (``classify_channel_category`` would otherwise bucket "lookonchain"
#: under ``other`` — no news/alpha keyword hits — and silently skip it).
ANALYST_CHANNELS: tuple[str, ...] = tuple(
    c.strip().lstrip("@")
    for c in os.getenv(
        "RMI_TG_NEWS_ANALYST_CHANNELS",
        "lookonchain,spotonchain,ArkhamIntel,OnChainWizard,nansen_ai,nansen_digest,"
        "glassnode,cryptowhaletrades,chainfeeds,intotheblock,SantimentFeed,"
        "duneanalytics,defillama,MessariCrypto,tokenterminal,DelphiDigital",
    ).split(",")
    if c.strip()
)

#: Scam-call / shill-intel channels (2026-08-29 content-flow overhaul).
#: The tg_news sweep reads these AND every discovered scam-classified
#: channel, tags their articles ``category="scam"``, and the routed lane
#: posts the scored digest to the ALERTS channel (@rmialerts — the channel
#: whose doctrine admits scam/rug detections) — never the news channel.
#: Handles here are force-swept as ``scam`` even when discovery is off;
#: the full scam_watch registry (hundreds of call channels) stays the
#: scanner's job — this is the high-signal call-out subset the feed reads.
SCAM_CALL_CHANNELS: tuple[str, ...] = tuple(
    c.strip().lstrip("@")
    for c in os.getenv(
        "RMI_TG_NEWS_SCAM_CHANNELS",
        "thepumpalert,CallAnalyser,walloftrophies,FuzzLandAlert,BeosinAlert,"
        "ScamSniffer,rektnews,zachxbt,SlowMist",
    ).split(",")
    if c.strip()
)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return list(default)
    parts = [p.strip().lstrip("@") for p in re_split(raw)]
    return [p for p in parts if p]


def re_split(value: str) -> list[str]:
    """Split an env list on commas, pipes, or whitespace."""
    return [p for p in value.replace(",", "|").replace(" ", "|").split("|") if p]


TG_NEWS_ENABLED = _env_bool("RMI_TG_NEWS_CHANNEL_ENABLED", True)


TG_NEWS_INTERVAL_MINUTES = _env_int("RMI_CHANNEL_TG_NEWS_INTERVAL_MINUTES", 30)


TG_NEWS_SWEEP_HOURS = _env_int("RMI_TG_NEWS_SWEEP_HOURS", 6)


TG_NEWS_PER_CHANNEL_LIMIT = _env_int("RMI_TG_NEWS_PER_CHANNEL_LIMIT", 50)


TG_NEWS_MAX_CHANNELS = _env_int("RMI_TG_NEWS_MAX_CHANNELS", 200)


TG_NEWS_POST_LIMIT = _env_int("RMI_TG_NEWS_POST_LIMIT", 5)


TG_NEWS_PERSIST = _env_bool("RMI_TG_NEWS_PERSIST", True)


TG_NEWS_RAG = _env_bool("RMI_TG_NEWS_RAG", True)


TG_NEWS_AUTO_DISCOVER = _env_bool("RMI_TG_NEWS_AUTO_DISCOVER", True)


TG_NEWS_CHANNELS = _env_list("RMI_TG_NEWS_CHANNELS", list(_DEFAULT_CHANNELS))


TME_RESOLVE_ENABLED = _env_bool("RMI_NEWS_TME_RESOLVE_ENABLED", True)


_CHANNEL_OWN_KEYWORDS: tuple[str, ...] = ("rugmunch", "rugmunchbot", "cryptorugmunch")


_CHANNEL_ALPHA_KEYWORDS: tuple[str, ...] = (
    "alpha",
    "whale",
    "whales",
    "signal",
    "signals",
    "calls",
    "call",
    "gem",
    "gems",
    "100x",
    "1000x",
    "moon",
    "mooner",
    "plays",
    "insider",
    "sniper",
    "smart money",
    "accumulation",
    "early entries",
    "pump group",
    "pnd",
)


_CHANNEL_NEWS_KEYWORDS: tuple[str, ...] = (
    "news",
    "cryptonews",
    "crypto",
    "coin",
    "coins",
    "finance",
    "market",
    "markets",
    "headlines",
    "daily",
    "today",
    "update",
    "updates",
    "media",
    "journal",
    "wire",
    "insight",
    "insights",
    "briefing",
    "report",
    "bulletin",
    "digest",
    "magazine",
    "times",
    "cointelegraph",
    "coindesk",
    "decrypt",
    "block",
    "bitcoin",
    "ethereum",
    "solana",
    "altcoin",
    "macro",
    "trading",
    "slate",
    "guru",
    "watcher",
    # Security/exploit alert signals — discovered security firms and exploit
    # alert channels ("PeckShield Alert", "CertiK Alert", "BlockSec") would
    # otherwise fall through to ``other`` and be dropped from the sweep.
    "alert",
    "alerts",
    "security",
    "exploit",
    "exploits",
    "intel",
    "audit",
    "phishing",
    "breach",
)
