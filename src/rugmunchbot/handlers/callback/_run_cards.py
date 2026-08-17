"""run_* callback usage cards (extracted from callback.py).

Batch 11 de-godding: the static ``_RUN_CARDS`` usage-card map lives here.
The ``run_*`` dispatcher (``_dispatch_run_cmd``) stays in ``callback.__init__``
so tests can monkeypatch the inline fetchers on the same module namespace.
"""

from __future__ import annotations

# Usage cards shown when a user taps a command button that needs arguments.
_RUN_CARDS: dict[str, str] = {
    "scan": (
        "\U0001f6e1\ufe0f <b>Token Security Scan</b>\n\n"
        "Usage: <code>/scan &lt;address&gt; [chain]</code>\n\n"
        "Or just paste any contract address \u2014 I auto-detect it!\n\n"
        "\U0001f4a1 Checks honeypots, rug pulls, bundles, fake volume, "
        "dev history and liquidity across 25+ chains.\n\n"
        "\U0001f52c Want more? Use <code>/deepscan &lt;address&gt;</code> "
        "for the full multi-phase forensic audit."
    ),
    "deepscan": (
        "\U0001f52c <b>Deep Token Scan</b>\n\n"
        "Usage: <code>/deepscan &lt;address&gt; [chain]</code>\n\n"
        "Runs ALL phases: market data \u2192 contract security \u2192 holder "
        "analysis \u2192 deployer fingerprint \u2192 funding analysis \u2192 "
        "AI risk verdict.\n\n"
        "\u23f3 Slower than /scan (up to ~2 min) but far more detailed."
    ),
    "sentinelscan": (
        "\U0001f6e1\ufe0f <b>AEGIS Deep Scan</b>\n\n"
        "Usage: <code>/sentinelscan &lt;address&gt; [chain]</code>\n\n"
        "Runs the full 9-module forensic pipeline: holder HHI, LP lock, "
        "dev reputation, contract authority, proxy, bundle, wash trading "
        "and honeypot analysis.\n\n"
        "\u23f3 Slowest scan (\u2248 1-2 min) \u2014 most thorough."
    ),
    "market": (
        "\U0001f4ca <b>Market Overview</b>\n\n"
        "Global market cap, 24h volume, BTC/ETH dominance, fear & greed "
        "index and top-asset movers \u2014 refreshed live from the backend."
    ),
    "movers": ("\U0001f680 <b>24h Market Movers</b>\n\nTop gainers and losers across the market over the last 24h."),
    "launches": (
        "\U0001f195 <b>New Token Launches</b>\n\n"
        "Freshly launched tokens with bundler flags \u2014 always "
        "/scan before buying."
    ),
    "whales": (
        "\U0001f40b <b>Whale Activity</b>\n\nLarge buy/sell moves detected across Solana/Ethereum/Bitcoin pairs."
    ),
    "rugcheck": (
        "\U0001f6a8 <b>Rug Pull Checklist</b>\n\n"
        "Usage: <code>/rugcheck &lt;address&gt;</code>\n\n"
        "Quick red-flag checklist: LP lock, mint authority, holder "
        "concentration, taxes and deployer history."
    ),
    "portfolio": (
        "\U0001f4c1 <b>Portfolio</b>\n\n"
        "Usage: <code>/portfolio &lt;wallet_address&gt;</code>\n\n"
        "Scans a wallet's token holdings \u2014 balances, values and risk "
        "flags. Track favorites with <code>/watch &lt;address&gt;</code>."
    ),
    "watchlist": (
        "\U0001f440 <b>Watchlist</b>\n\n"
        "Usage: <code>/watch &lt;address&gt;</code> to add \u2022 "
        "<code>/watchlist</code> to view \u2022 <code>/unwatch &lt;address&gt;</code> to remove."
    ),
    "alerts": (
        "\U0001f514 <b>Price Alerts</b>\n\n"
        "Usage: <code>/alert ABOVE &lt;addr&gt; &lt;price&gt;</code> or "
        "<code>/alert BELOW &lt;addr&gt; &lt;price&gt;</code>. Manage with "
        "<code>/alerts</code> \u2014 <b>Scout+ tiers</b>."
    ),
    "alert": (
        "\U0001f4c4 <b>Set a Price Alert</b>\n\n"
        "Usage: <code>/alert ABOVE &lt;addr&gt; &lt;price&gt;</code>\n"
        "       <code>/alert BELOW &lt;addr&gt; &lt;price&gt;</code>\n"
        "       <code>/alert CHANGE_5 &lt;addr&gt;</code>\n\n"
        "Manage all alerts with <code>/alerts</code> \u2014 <b>Scout+ tiers</b>."
    ),
    "watch": (
        "\u2b50 <b>Watch a Token</b>\n\n"
        "Usage: <code>/watch &lt;address&gt; [chain]</code>\n\n"
        "Add a token to your watchlist \u2014 view it anytime with "
        "<code>/watchlist</code> and remove with <code>/unwatch &lt;address&gt;</code>."
    ),
    "whale": (
        "\U0001f40b <b>Whale Movement Alerts</b>\n\n"
        "Usage: <code>/whale &lt;address&gt;</code>\n\n"
        "Subscribe to whale movement alerts for a token and get notified "
        "when big wallets move."
    ),
    "webhook": (
        "\U0001f517 <b>Custom Webhooks</b>\n\n"
        "Usage: <code>/webhook</code>\n\n"
        "Manage custom webhook notifications for price, liquidity and "
        "security events \u2014 <b>Pro tier</b>."
    ),
    "compare": (
        "\u2696\ufe0f <b>Compare Tokens</b>\n\n"
        "Usage: <code>/compare &lt;addr1&gt; &lt;addr2&gt;</code>\n\n"
        "Side-by-side risk score, liquidity, price and holder metrics in one view."
    ),
    "social": (
        "\U0001f4e3 <b>Social Signals</b>\n\n"
        "Gauge community buzz before you buy:\n"
        "\u2022 <code>/hype &lt;symbol&gt;</code> \u2014 0-100 hype score incl. social velocity\n"
        "\u2022 <code>/sentiment</code> \u2014 market-wide fear & greed\n"
        "\u2022 <code>/news</code> \u2014 headlines with sentiment & bias\n"
        "\u2022 <code>/trending</code> \u2014 what the crowd is piling into\n\n"
        "Pair social signals with a <code>/scan</code> for the full picture."
    ),
    "smartmoney": (
        "\U0001f9e0 <b>Smart Money Detection</b>\n\n"
        "Usage: <code>/smartmoney &lt;address&gt;</code>\n\n"
        "Flags wallets with profitable histories \u2014 snipers, KOLs and repeat winners."
    ),
    "trace": (
        "\U0001f50d <b>Fund Flow Trace</b>\n\n"
        "Usage: <code>/trace &lt;address&gt;</code>\n\n"
        "Cross-chain fund flow tracing \u2014 <b>Hunter+ tier</b>."
    ),
    "ta": (
        "\U0001f4ca <b>Technical Analysis</b>\n\n"
        "Usage: <code>/ta &lt;address&gt;</code>\n\n"
        "Momentum, volume trend and buy/sell pressure signals for any token."
    ),
    "help": (
        "\U0001f4d6 <b>Help & About</b>\n\n"
        "Use <code>/help</code> for the full command reference, or browse "
        "the categories from the main menu.\n\n"
        "Community: \U0001f4e2 Alerts channel \u00b7 \U0001f4ac Chat group \u00b7 "
        "\U0001f310 rugmunch.io"
    ),
    "quick": (
        "\U0001fa99 <b>Quick Price Check</b>\n\n"
        "Usage: <code>/quick &lt;symbol&gt;</code>\n\n"
        "Same as typing <code>$TOKEN</code> \u2014 instant price check."
    ),
    "wallet": (
        "\U0001f9db <b>Wallet Forensics</b>\n\n"
        "Usage: <code>/wallet &lt;address&gt;</code>\n\n"
        "Holdings, risk score, entity attribution, scam exposure, "
        "smart money overlap and funding sources."
    ),
    "deployer": (
        "\U0001f575\ufe0f <b>Deployer Fingerprint</b>\n\n"
        "Usage: <code>/deployer &lt;address&gt;</code>\n\n"
        "Check the deployer's history for past rugs and suspicious patterns."
    ),
    "hype": (
        "\U0001f525 <b>Hype Score</b>\n\n"
        "Usage: <code>/hype &lt;symbol or address&gt;</code>\n\n"
        "Quick 0-100 community hype score for any token."
    ),
    "investigate": (
        "\U0001f50e <b>Deep Investigation</b>\n\n"
        "Usage: <code>/investigate &lt;address&gt;</code>\n\n"
        "Full forensic investigation report \u2014 <b>Pro tier</b>."
    ),
    "price": (
        "\U0001f4b0 <b>Token Price</b>\n\n"
        "Usage: <code>/price &lt;address&gt; [chain]</code>\n\n"
        "Live price by contract address, or just type <code>$SYMBOL</code>."
    ),
    "chart": (
        "\U0001f4c9 <b>Price Chart</b>\n\n"
        "Usage: <code>/chart &lt;address&gt; [chain]</code>\n\n"
        "Generates a chart image (OHLCV), "
        "with technical indicators and trend analysis."
    ),
    "rugmap": (
        "\U0001f5fa\ufe0f <b>RugMap Bubble Map</b>\n\n"
        "Usage: <code>/rugmap &lt;address&gt; [chain]</code>\n\n"
        "Generates a bubble-map image of wallet clusters, fund flows, "
        "dev wallets and smart money."
    ),
    "report": (
        "\U0001f6ab <b>Report a Scam</b>\n\n"
        "Usage: <code>/report &lt;address&gt; [reason]</code>\n\n"
        "Flags a token address as a scam. Reports feed the shared "
        "denylist and go straight to the Rug Munch team."
    ),
    "dex": (
        "\U0001f4e6 <b>DEX Pairs & Liquidity</b>\n\n"
        "Usage: <code>/dex &lt;address&gt; [chain]</code>\n\n"
        "Trading pairs, liquidity pools and DEX referral links."
    ),
    "holders": (
        "\U0001f465 <b>Holder Analysis</b>\n\n"
        "Usage: <code>/holders &lt;address&gt;</code>\n\n"
        "Top holders & concentration \u2014 <b>Scout+ tier</b>."
    ),
    "simulate": (
        "\U0001f4b5 <b>Trade Simulator</b>\n\n"
        "Usage: <code>/simulate &lt;address&gt; [amount]</code>\n\n"
        "Simulate a buy/sell and see estimated P&L."
    ),
    "ask": (
        "\U0001f50d <b>Deep AI Answers</b>\n\n"
        "Usage: <code>/ask &lt;question&gt;</code>\n\n"
        "RAG + LLM synthesis from the RMI intelligence base.\n\n"
        "Example: /ask <code>how do honeypots work</code>"
    ),
    "research": (
        "\U0001f9e0 <b>RAG Deep Research</b>\n\n"
        "Usage: <code>/research &lt;query&gt;</code>\n\n"
        "Dives into threat intel, scam patterns and agent memory \u2014 "
        "<b>Hunter+ tier</b>.\n\n"
        "Example: /research <code>wash trading detection</code>"
    ),
    "chat": (
        "\U0001f4ac <b>Freestyle AI Chat</b>\n\n"
        "Usage: <code>/chat &lt;question&gt;</code>\n\n"
        "Or just type any message \u2014 no command needed. I answer "
        "crypto, DeFi, wallet and coding questions with RAG context.\n\n"
        "Example: /chat <code>write a Solidity ERC-20 with a tax</code>"
    ),
}
