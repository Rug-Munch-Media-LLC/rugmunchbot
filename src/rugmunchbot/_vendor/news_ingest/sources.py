"""News source definitions for RSS feeds and Telegram channels.

This module defines the comprehensive list of news sources that feed into
the RAG pipeline, including both RSS feeds and Telegram channels.
Each source has: name, url, type (rss/telegram), category, and enabled flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class NewsSource:
    """Definition of a news source."""

    name: str
    url: str
    type: Literal["rss", "telegram"]
    category: Literal["news", "security", "defi", "scam", "alpha"]
    enabled: bool = True


# RSS Feeds (50+)
RSS_FEEDS: list[NewsSource] = [
    # Major Crypto News Sites
    NewsSource("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "rss", "news"),
    NewsSource("CoinTelegraph", "https://cointelegraph.com/rss", "rss", "news"),
    NewsSource("The Block", "https://www.theblock.co/rss.xml", "rss", "news"),
    NewsSource("Decrypt", "https://decrypt.co/feed", "rss", "news"),
    NewsSource("Bankless", "https://bankless.substack.com/feed", "rss", "alpha"),
    NewsSource("The Defiant", "https://thedefiant.io/feed/", "rss", "defi"),
    NewsSource("Messari", "https://messari.io/rss", "rss", "news"),
    NewsSource("CoinGecko Blog", "https://www.coingecko.com/en/blog/rss.xml", "rss", "news"),
    NewsSource("Chainalysis Blog", "https://www.chainalysis.com/blog/feed/", "rss", "security"),
    NewsSource("PeckShield Blog", "https://www.peckshield.com/news/atom.xml", "rss", "security"),
    NewsSource("SlowMist Blog", "https://www.slowmist.com/articles/feed", "rss", "security"),
    NewsSource("Rekt.news", "https://rekt.news/rss.xml", "rss", "security"),
    NewsSource("DeFiLlama Blog", "https://defillama.com/blog/rss.xml", "rss", "defi"),
    # DeFi Protocol Blogs
    NewsSource("Uniswap Blog", "https://uniswap.org/blog/rss.xml", "rss", "defi"),
    NewsSource("Aave Blog", "https://aave.com/blog/rss.xml", "rss", "defi"),
    NewsSource("Compound Blog", "https://compound.finance/blog/rss.xml", "rss", "defi"),
    NewsSource("MakerDAO Blog", "https://blog.makerdao.com/rss.xml", "rss", "defi"),
    NewsSource("Lido Blog", "https://blog.lido.fi/rss.xml", "rss", "defi"),
    NewsSource("EigenLayer Blog", "https://blog.eigenlayer.xyz/rss.xml", "rss", "defi"),
    NewsSource("Celestia Blog", "https://blog.celestia.org/rss.xml", "rss", "defi"),
    NewsSource("Solana Blog", "https://solana.com/blog/rss.xml", "rss", "defi"),
    NewsSource("Helius Blog", "https://helius.dev/blog/rss.xml", "rss", "defi"),
    NewsSource("Jito Blog", "https://jito.xyz/blog/rss.xml", "rss", "defi"),
    NewsSource("Jupiter Blog", "https://jup.ag/blog/rss.xml", "rss", "defi"),
    NewsSource("Pyth Blog", "https://pyth.network/blog/rss.xml", "rss", "defi"),
    NewsSource("Chainlink Blog", "https://blog.chainlink.org/rss.xml", "rss", "defi"),
    NewsSource("Wormhole Blog", "https://wormhole.com/blog/rss.xml", "rss", "defi"),
    NewsSource("LayerZero Blog", "https://layerzero.network/blog/rss.xml", "rss", "defi"),
    NewsSource("Across Blog", "https://across.to/blog/rss.xml", "rss", "defi"),
    NewsSource("Stargate Blog", "https://stargate.finance/blog/rss.xml", "rss", "defi"),
    NewsSource("0x Blog", "https://blog.0xproject.com/rss.xml", "rss", "defi"),
    NewsSource("Matcha Blog", "https://matcha.xyz/blog/rss.xml", "rss", "defi"),
    NewsSource("DexScreener Blog", "https://dexscreener.com/blog/rss.xml", "rss", "defi"),
    NewsSource("GeckoTerminal Blog", "https://www.geckoterminal.com/blog/rss.xml", "rss", "defi"),
    NewsSource("Bubblemaps Blog", "https://bubblemaps.io/blog/rss.xml", "rss", "security"),
    NewsSource("Arkham Blog", "https://arkhamintelligence.com/blog/rss.xml", "rss", "security"),
    NewsSource("Etherscan Blog", "https://etherscan.io/blog/rss.xml", "rss", "news"),
    # Layer 2 & Scaling Solutions
    NewsSource("BSC News", "https://www.bnbchain.com/en/blog/rss.xml", "rss", "news"),
    NewsSource("Polygon Blog", "https://polygon.technology/blog/rss.xml", "rss", "defi"),
    NewsSource("Arbitrum Blog", "https://arbitrum.io/blog/rss.xml", "rss", "defi"),
    NewsSource("Optimism Blog", "https://www.optimism.io/blog/rss.xml", "rss", "defi"),
    NewsSource("Base Blog", "https://base.org/blog/rss.xml", "rss", "defi"),
    NewsSource("Scroll Blog", "https://scroll.io/blog/rss.xml", "rss", "defi"),
    NewsSource("zkSync Blog", "https://zksync.io/blog/rss.xml", "rss", "defi"),
    NewsSource("Linea Blog", "https://linea.build/blog/rss.xml", "rss", "defi"),
    NewsSource("Mantle Blog", "https://mantle.xyz/blog/rss.xml", "rss", "defi"),
    NewsSource("Blast Blog", "https://blast.io/blog/rss.xml", "rss", "defi"),
    # Alternative L1s
    NewsSource("Sui Blog", "https://sui.io/blog/rss.xml", "rss", "defi"),
    NewsSource("Aptos Blog", "https://aptos.dev/blog/rss.xml", "rss", "defi"),
    NewsSource("TON Blog", "https://blog.ton.org/rss.xml", "rss", "defi"),
    NewsSource("Near Blog", "https://near.org/blog/rss.xml", "rss", "defi"),
    NewsSource("Flow Blog", "https://onflow.org/blog/rss.xml", "rss", "defi"),
    # Additional News Sources
    NewsSource("CryptoNews", "https://cryptonews.com/news/rss", "rss", "news"),
    NewsSource("Bitcoinist", "https://bitcoinist.com/feed/", "rss", "news"),
    NewsSource("NewsBTC", "https://www.newsbtc.com/feed/", "rss", "news"),
    NewsSource("CoinPedia", "https://www.coinpedia.org/feed/", "rss", "news"),
    NewsSource("AMBCrypto", "https://ambcrypto.com/feed/", "rss", "news"),
    NewsSource(
        "FXStreet Crypto", "https://www.fxstreet.com/cryptocurrencies/news/rss", "rss", "news"
    ),
    NewsSource(
        "ValueWalk Crypto", "https://www.valuewalk.com/category/cryptocurrency/feed/", "rss", "news"
    ),
    NewsSource("U.Today", "https://u.today/rss", "rss", "news"),
    NewsSource("CryptoPotato", "https://cryptopotato.com/feed/", "rss", "news"),
    NewsSource("ZyCrypto", "https://zycrypto.com/feed/", "rss", "news"),
    NewsSource("CoinGape", "https://coingape.com/feed/", "rss", "news"),
    NewsSource("CryptoSlate", "https://cryptoslate.com/feed/", "rss", "news"),
    NewsSource("CryptoBriefing", "https://cryptobriefing.com/feed/", "rss", "news"),
    NewsSource("CoinJournal", "https://coinjournal.net/feed/", "rss", "news"),
]

# Telegram Channels (20+)
TELEGRAM_CHANNELS: list[NewsSource] = [
    # Security & Exploit Alerts
    NewsSource("@peckshield", "https://t.me/peckshield", "telegram", "security"),
    NewsSource("@zachxbt", "https://t.me/zachxbt", "telegram", "security"),
    NewsSource("@blockaid", "https://t.me/blockaid", "telegram", "security"),
    NewsSource("@certikalert", "https://t.me/certikalert", "telegram", "security"),
    NewsSource("@slowmist", "https://t.me/slowmist", "telegram", "security"),
    NewsSource("@beosinalert", "https://t.me/beosinalert", "telegram", "security"),
    NewsSource("@chainalysis", "https://t.me/chainalysis", "telegram", "security"),
    NewsSource("@elliptic", "https://t.me/elliptic", "telegram", "security"),
    NewsSource("@trmlabs", "https://t.me/trmlabs", "telegram", "security"),
    NewsSource("@hashex", "https://t.me/hashex", "telegram", "security"),
    NewsSource("@pwnfoo", "https://t.me/pwnfoo", "telegram", "security"),
    NewsSource("@angularrobot", "https://t.me/angularrobot", "telegram", "security"),
    # News & Analysis
    NewsSource("@samczsun", "https://t.me/samczsun", "telegram", "alpha"),
    NewsSource("@0xfoobar", "https://t.me/0xfoobar", "telegram", "alpha"),
    NewsSource("@hacxyk", "https://t.me/hacxyk", "telegram", "alpha"),
    NewsSource("@rektblog", "https://t.me/rektblog", "telegram", "security"),
    NewsSource("@defillama", "https://t.me/defillama", "telegram", "defi"),
    NewsSource("@dexscreener", "https://t.me/dexscreener", "telegram", "defi"),
    NewsSource("@geckoterminal", "https://t.me/geckoterminal", "telegram", "defi"),
    NewsSource("@bubblemaps", "https://t.me/bubblemaps", "telegram", "security"),
    NewsSource("@arkhamintel", "https://t.me/arkhamintel", "telegram", "security"),
    NewsSource("@etherscan", "https://t.me/etherscan", "telegram", "news"),
    # Additional Alpha Channels
    NewsSource("@whalealert", "https://t.me/whalealert", "telegram", "alpha"),
    NewsSource("@lookonchain", "https://t.me/lookonchain", "telegram", "alpha"),
    NewsSource("@spotonchain", "https://t.me/spotonchain", "telegram", "alpha"),
    NewsSource("@glassnode", "https://t.me/glassnode", "telegram", "alpha"),
    NewsSource("@intotheblock", "https://t.me/intotheblock", "telegram", "alpha"),
    NewsSource("@santimentfeed", "https://t.me/santimentfeed", "telegram", "alpha"),
    NewsSource("@coinglass", "https://t.me/coinglass", "telegram", "alpha"),
    NewsSource("@coinalyze", "https://t.me/coinalyze", "telegram", "alpha"),
]

# Combined sources list
ALL_SOURCES: list[NewsSource] = RSS_FEEDS + TELEGRAM_CHANNELS

# Convenience properties
RSS_SOURCE_COUNT = len(RSS_FEEDS)
TELEGRAM_SOURCE_COUNT = len(TELEGRAM_CHANNELS)
TOTAL_SOURCE_COUNT = len(ALL_SOURCES)
