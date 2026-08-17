"""Tier-2 local intent matching (canned, zero-backend answers).

Extracted from ``chat_ai/_helpers.py`` during the de-godding pass: the
intent-rule table and :func:`local_ai_response`. Used for gibberish that is
not worth an LLM call and as an offline fallback when the backend chat is
unavailable. The self-improvement / self-description / identity rules are
registered FIRST so they win over the generic capability menu whenever both
patterns match. Pure — ``_helpers.py`` re-imports from here; behavior is
byte-for-byte identical.
"""

from __future__ import annotations

import re
from typing import Any

from app.domains.telegram.rugmunchbot.formatters import footer_links
from app.domains.telegram.rugmunchbot.handlers.chat_ai._classify import _SELF_IMPROVEMENT_RE

_SELF_IMPROVEMENT_ANSWER = (
    "\U0001f6e0\ufe0f <b>What would genuinely help me</b>\n\n"
    "Love this question! I already run on a solid stack \u2014 token "
    "security scans, wallet forensics, cross-chain tracing, live prices, "
    "whale alerts, and a 91K+ doc intel library across 25+ chains. But if "
    "you're asking what would make me <i>better</i> at my job:\n\n"
    "\U0001f4c8 <b>Real-time price &amp; orderbook feeds</b> \u2014 "
    "sub-second prices and depth data so my market answers never go stale.\n"
    "\U0001f4cb <b>Full on-chain history</b> \u2014 complete transfer and "
    "trade history for any token or wallet, so traces and funding paths go "
    "deeper than recent activity.\n"
    "\U0001f465 <b>More labeled wallet data</b> \u2014 exchanges, funds, "
    "scammer addresses and airdrop farmers, so cluster analysis names the "
    "players instead of just linking them.\n"
    "\U0001f9e0 <b>A stronger LLM</b> \u2014 bigger context and better "
    "reasoning means sharper answers on complex multi-step investigations.\n"
    "\U0001f4f0 <b>Live news &amp; webhooks</b> \u2014 push feeds for "
    "exploits, launches and whale moves, so I can flag threats the moment "
    "they happen instead of waiting for someone to ask.\n\n"
    "Each of those turns me from reactive to proactive \u2014 faster, "
    "deeper, safer intel for the whole community."
    f"{footer_links()}"
)

# ══════════════════════════════════════════════
# SELF-DESCRIPTION / IDENTITY QUESTIONS
# "who are you / what can you do / what are your best qualities" deserves a
# THOROUGH canned answer — not the short creator blurb and not the command
# menu. Identity questions used to fall through to the LLM, which emitted
# markdown tables that got cut mid-block and rendered as a wall of pipes.
# This rule fires BEFORE the creator rule and the generic capability-menu
# rule so every identity phrasing gets the full, table-free self-description.
# EXCEPTION: "who are you" is claimed by the dedicated identity/creator rule
# (persona answer), not this one — it is deliberately absent from the regex
# above so the persona answer wins over the capabilities card.
# HTML-safe (balanced <b>/<code>, no raw < >, no tables) — sent with
# ParseMode.HTML by _reply_local_intent.
# ══════════════════════════════════════════════

_SELF_DESCRIPTION_RE = re.compile(
    r"\b(?:"
    # NOTE: "who are you" is intentionally NOT here — it must win the persona /
    # creator answer, so it is left to the identity rule (see _INTENT_RULES).
    # Plain capability-word questions ("what are your capabilities") are left
    # to the generic capability-menu rule for the same reason.
    r"who '?re you|who r you|"
    r"tell me about (?:yourself|you|this bot)|"
    r"about (?:yourself|you|this bot)|"
    r"introduce yourself|describe yourself|"
    r"what can you do|what do you do|what else can you (?:do|help with)|"
    r"what are your (?:best )?(?:qualities|strengths|skills|features|abilities)|"
    r"what '?re your (?:qualities|strengths|skills|features|abilities)|"
    r"what is your (?:best )?(?:quality|strength|skill|feature)|"
    r"what '?s your (?:best )?(?:quality|strength|skill|feature)|"
    r"what are you (?:good at|capable of)|"
    r"your best qualities|your strengths|your skills"
    r")\b",
    re.IGNORECASE,
)

_SELF_DESCRIPTION_ANSWER = (
    "\U0001f575\ufe0f <b>Rug Munch Capabilities</b> \u2014 who I am &amp; what I can do\n\n"
    "I'm <b>Rug Munch Bot</b> \u2014 built by <b>@cryptorugmunch</b> &amp; "
    "the team at <b>Rug Munch Intelligence</b>, owned &amp; operated by "
    "<b>Rug Munch Media LLC</b> (Wyoming, USA) \u2014 the crypto security "
    "&amp; on-chain intelligence analyst, built to "
    "protect retail investors from scams. I never invent numbers: every "
    "metric I quote is grounded in real scan data, live market feeds, or "
    "the intel library.\n\n"
    "<b>\U0001f6e1\ufe0f Security forensics</b> \u2014 full token audits: "
    "honeypots, mintable supply, rug-pull risks, liquidity health, "
    "deployer fingerprints.\n"
    "<b>\U0001f464 Wallet intelligence</b> \u2014 wallet forensics, "
    "portfolio reads, funding paths, wallet clustering &amp; smart-money flow.\n"
    "<b>\U0001f4ca Market analysis</b> \u2014 live prices, market cap, "
    "volume, macro market reads (BTC/ETH, fear &amp; greed).\n"
    "<b>\U0001f4c8 Technical analysis</b> \u2014 RSI, MACD, "
    "support/resistance, chart patterns &amp; signal scoring.\n"
    "<b>\U0001f3af Prediction markets</b> \u2014 Polymarket odds, "
    "election &amp; event markets.\n"
    "<b>\U0001f6a8 Scam detection</b> \u2014 impersonation checks, MEV risk, "
    "death-clock survival predictions, known-scam denylist.\n"
    "<b>\U0001f4f0 News &amp; alpha</b> \u2014 live headlines, sentiment, "
    "whale &amp; alpha signals from the Rug Munch channels.\n"
    "<b>\U0001f4da RAG-grounded knowledge</b> \u2014 answers backed by the "
    "91K+ document intel library across 25+ chains.\n"
    "<b>\u26a1 Live data, multi-chain</b> \u2014 EVM chains + Solana, "
    "refreshed in real time per request.\n"
    "<b>\U0001f6ab Zero fabrication</b> \u2014 no invented metrics, "
    "addresses or on-chain events, ever. If I don't have the data, I say so.\n\n"
    "Want specifics? Try <code>/scan &lt;addr&gt;</code>, "
    "<code>/wallet &lt;addr&gt;</code>, <code>/ta &lt;addr&gt;</code> or "
    "<code>/market</code> \u2014 or just ask in plain text."
    f"{footer_links()}"
)

# ══════════════════════════════════════════════
# PRESENCE CHECKS ("are you there" / "anyone home")
# A presence check is NOT a question — it gets an instant 1-2 line warm
# acknowledgment instead of the full capabilities card, and never falls
# through to the slow LLM/RAG path. Registered BEFORE the greeting rule so
# presence phrasings ("you there", "u there", "there?") win over the long
# "Yo! I'm here" capabilities card.
# ══════════════════════════════════════════════

_PRESENCE_RE = re.compile(
    r"\b(are you there|you there|anyone there|anyone home|you alive|u there)\b"
    # Bare "there?" is a presence check; \bthere\b alone is far too broad.
    r"|\bthere\?"
)

_PRESENCE_ANSWER = (
    "\U0001f44b <b>I'm here!</b>\n\n"
    "Drop a token address to scan, ask a price (<code>$TOKEN</code>), or "
    "ask any DeFi/security question \u2014 I'll dig right in."
)

# ══════════════════════════════════════════════
# TIER-2 LOCAL INTENT MATCHING (ported from handlers/message.py)
# Canned, zero-backend answers for greetings / feature intents. Used for
# gibberish that is not worth an LLM call and as an offline fallback when the
# backend chat is unavailable.
# ══════════════════════════════════════════════

_INTENT_RULES: list[tuple[re.Pattern[str], str]] = [
    # Self-improvement questions MUST win over the capability menu (and over
    # incidental keyword matches like "scan"/"news" in e.g. "what would help
    # you scan tokens better"), so this rule is registered FIRST.
    (
        _SELF_IMPROVEMENT_RE,
        _SELF_IMPROVEMENT_ANSWER,
    ),
    # Identity / self-description questions MUST win over the short creator
    # blurb and the command menu — registered right after self-improvement.
    (
        _SELF_DESCRIPTION_RE,
        _SELF_DESCRIPTION_ANSWER,
    ),
    (
        re.compile(
            r"\b(scan|check|audit|rug|scam|honeypot|safu|drain|phish|malicious|"
            r"dangerous|secure|risky|safe(ty)?|is this safe|is it safe|trustworthy)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f50d <b>Token Security Scan</b>\n\n"
            "Use <code>/scan &lt;address&gt;</code> to run a full security audit. "
            "I check for:\n"
            "\u2022 \U0001f36f Honeypot detection\n"
            "\u2022 \U0001f3c3 Rug pull risks\n"
            "\u2022 \U0001f4e6 Bundle / wash trading\n"
            "\u2022 \U0001f9db Dev wallet history\n"
            "\u2022 \U0001f4ca Liquidity & holder analysis\n\n"
            "Just paste any contract address and I'll auto-detect it!"
        ),
    ),
    (
        re.compile(
            r"\b(price|chart|worth|value|how much|cost|mc[aap]+|market cap|volume|"
            r"liquidity|pump|dump|buy|sell|token price|usd)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f4ca <b>Price & Market Data</b>\n\n"
            "Use <code>/quick &lt;symbol&gt;</code> or <code>$TOKEN</code> for instant prices.\n"
            "Use <code>/chart &lt;address&gt;</code> for technical analysis charts.\n"
            "Use <code>/scan &lt;address&gt;</code> for full market data.\n\n"
            "\U0001f4b0 Real-time market data for any token."
        ),
    ),
    (
        re.compile(
            r"\b(wallet|portfolio|balance|holdings|my token|my coin|bag|"
            r"hodl|fund|money)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f9db <b>Wallet Analysis</b>\n\n"
            "Use <code>/wallet &lt;address&gt;</code> to analyze any wallet.\n"
            "Use <code>/portfolio</code> to view your tracked wallets.\n\n"
            "I show:\n"
            "\u2022 \U0001f4b0 Token balances & values\n"
            "\u2022 \U0001f504 Recent transaction history\n"
            "\u2022 \u26a0\ufe0f Risk metrics & scam checks\n"
            "\u2022 \U0001f517 Counterparty connections"
        ),
    ),
    (
        re.compile(r"\b(compare|vs |versus|better than|which (is|one)|difference)\b", re.IGNORECASE),
        (
            "\u2696\ufe0f <b>Token Comparison</b>\n\n"
            "Use <code>/compare &lt;addr1&gt; &lt;addr2&gt;</code> to pit two tokens "
            "side-by-side.\n\n"
            "I'll show you both risk scores, prices, liquidity, and holder metrics "
            "in one view!"
        ),
    ),
    (
        re.compile(
            r"\b(trending|hot|popular|what to buy|best coin|moon|gem|"
            r"next 100x|top token|gainers|movers)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f525 <b>Trending Tokens</b>\n\n"
            "Check <code>/trending</code> for the hottest tokens right now.\n"
            "I show real-time data on:\n"
            "\u2022 \U0001f4c8 Top gainers\n"
            "\u2022 \U0001f4a7 Highest volume\n"
            "\u2022 \U0001f195 New pairs\n"
            "\u2022 \U0001f3e0 Most holders"
        ),
    ),
    (
        re.compile(
            r"\b(alert|notify|watch|track|monitor|notification|remind|follow)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f514 <b>Price Alerts</b>\n\n"
            "Use <code>/watch &lt;address&gt;</code> to add tokens to your watchlist.\n"
            "Use <code>/alerts</code> to manage your price alerts.\n\n"
            "\U0001f4cc <b>Scout+</b> tier gets real-time push alerts for price moves, "
            "large transactions, and security events."
        ),
    ),
    (
        re.compile(
            r"\b(mev|sandwich|frontrun|backrun|protect(ion)? (trade|swap)|"
            r"safe swap|slippage|gas optimal|mev blocker|flashbot)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f6e1\ufe0f <b>MEV Protection</b>\n\n"
            "Use <code>/mev &lt;token_address&gt;</code> to check sandwich attack risk "
            "and get safe swap parameters:\n"
            "• Frontrunning risk score\n"
            "• Recommended slippage tolerance\n"
            "• Optimal gas settings\n"
            "• MEV-blocker routing suggestion\n\n"
            "<i>Free during beta — protect your trades from MEV bots.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(impersonat|fake token|copycat|clone token|pretending to be|"
            r"fake version|counterfeit|fraudulent token)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f97e <b>Impersonation Detector</b>\n\n"
            "Use <code>/impersonation &lt;token_address&gt;</code> to audit for:\n"
            "• Name/symbol similarity to known tokens\n"
            "• Honeypot delegates & hidden permissions\n"
            "• Related malicious contracts\n\n"
            "<i>Free during beta — spot fake tokens before you buy.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(death clock|how long (will|does)|survival|when (will|does) (it|token) die|"
            r"rug countdown|abandonment|token lifespan|longevity)\b",
            re.IGNORECASE,
        ),
        (
            "\u23f0 <b>Death Clock</b>\n\n"
            "Use <code>/deathclock &lt;chain&gt; &lt;token_address&gt;</code> to predict:\n"
            "• Days until likely death (rug or abandonment)\n"
            "• Key risk factors\n"
            "• Survival probability\n\n"
            "<i>Free during beta — know the risk before you hold.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(new launch|just launched|fresh token|new token|recent launch|"
            "brand new|just dropped|newly created)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f525 <b>Token Launches</b>\n\n"
            "Use <code>/launches [chain]</code> to see new token launches with:\n"
            "• Auto-risk flags (low/medium/high)\n"
            "• Liquidity & holder counts\n"
            "• Direct scan buttons\n\n"
            "<i>Free during beta — discover new tokens early.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(cluster|related wallet|funding path|same owner|connected wallet|"
            r"wallet group|bundle|coordinated|sniper bot|deployer funded)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f578\ufe0f <b>Wallet Clustering</b>\n\n"
            "Use <code>/cluster &lt;wallet_address&gt;</code> to detect:\n"
            "• Related wallets by behavior/funding\n"
            "• Fund origin (CEX, mixer, other tokens)\n"
            "• Deployer-funded wallets (bundle detection)\n\n"
            "<i>Free during beta — uncover hidden wallet connections.</i>"
            f"{footer_links()}"
        ),
    ),
    # Presence checks must win over the generic greeting card (fast warm
    # 1-2 line reply).
    (
        _PRESENCE_RE,
        _PRESENCE_ANSWER,
    ),
    (
        re.compile(
            r"\b(hello|hi|hey|yo|sup|howdy|good morning|good evening|"
            r"gm|gn|what'?s up|welcome|are you (there|alive|up|online|working)|"
            r"you (there|alive|up|online|working)|anyone (there|home)|u there|"
            r"ping|wake up|respond)\b"
            r"|\bthere\?",
            re.IGNORECASE,
        ),
        (
            "\U0001f44b <b>Yo! I'm here.</b>\n\n"
            "I'm <b>Rug Munch Bot</b> \u2014 built by <b>@cryptorugmunch</b> "
            "&amp; the team at <b>Rug Munch Intelligence</b>, owned &amp; "
            "operated by <b>Rug Munch Media LLC</b> (Wyoming, USA). "
            "I scan tokens for rugs, profile wallets, track whales, and "
            "answer crypto questions backed by 91K+ intel documents across 25+ chains.\n\n"
            "\U0001f6e1\ufe0f <b>Quick start:</b>\n"
            "\u2022 Paste a contract address \u2192 instant scam scan\n"
            "\u2022 <code>/scan &lt;addr&gt;</code> \u2192 deep security audit\n"
            "\u2022 <code>/wallet &lt;addr&gt;</code> \u2192 wallet forensics\n"
            "\u2022 <code>/market</code> \u2192 live market overview\n"
            "\u2022 <code>$TOKEN</code> \u2192 price lookup\n"
            "\u2022 <code>/mev</code> \u2192 MEV risk & safe swap params\n"
            "\u2022 <code>/deathclock</code> \u2192 token survival prediction\n"
            "\u2022 <code>/help</code> \u2192 full command list\n\n"
            "Or just ask me anything about crypto \u2014 scams, hacks, market moves, "
            "regulations, whatever's on your mind."
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(r"\b(thank|thanks|ty|thx|appreciate|good bot|nice|great)\b", re.IGNORECASE),
        (
            "\U0001f64c You're welcome! Happy to help keep the crypto space safe.\n\n"
            "If you find this useful, tell your friends! \n"
            "Use <code>/refer</code> to get your referral link."
        ),
    ),
    (
        re.compile(
            r"\b(who made you|who created you|who built you|who developed you|"
            r"who runs you|who owns you|who is your creator|who is behind you|who are you)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f916 <b>I'm Rug Munch Bot</b>\n\n"
            "Made by <b>@cryptorugmunch</b> and the team at <b>Rug Munch "
            "Intelligence</b> \u2014 the crypto security &amp; on-chain intel "
            "platform \u2014 and owned &amp; operated by <b>Rug Munch Media "
            "LLC</b> (Wyoming, USA). I scan tokens, profile wallets, track "
            "whales, and answer crypto questions backed by 91K+ intel docs "
            "across 25+ chains."
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(help|can you|capabilities|capability|features|commands|what do|what are|"
            r"who are you|tell me about you|about you|about this bot|"
            r"introduce yourself|describe yourself|what can you do|what else can you|"
            r"show me what you|guide|tutorial|docs|documentation)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f916 <b>Rug Munch Capabilities</b>\n\n"
            "I'm a multi-chain crypto intelligence bot. Here's what I can do:\n\n"
            "\U0001f50d <code>/scan &lt;addr&gt;</code> \u2014 Full token security audit\n"
            "\U0001f9db <code>/wallet &lt;addr&gt;</code> \u2014 Wallet forensics\n"
            "\U0001f4ca <code>/quick &lt;symbol&gt;</code> \u2014 Price lookup\n"
            "\U0001f4c8 <code>/chart &lt;addr&gt;</code> \u2014 Technical analysis\n"
            "\u2696\ufe0f <code>/compare &lt;a&gt; &lt;b&gt;</code> \u2014 Side-by-side compare\n"
            "\U0001f525 <code>/trending</code> \u2014 Hot tokens by chain\n"
            "\U0001f4f0 <code>/news</code> \u2014 Crypto headlines\n"
            "\U0001f393 <code>/scamschool</code> \u2014 Learn scam patterns\n"
            "\U0001f6e1\ufe0f <code>/mev</code> \u2014 MEV risk & safe swap params\n"
            "\U0001f97e <code>/impersonation</code> \u2014 Detect fake tokens\n"
            "\u23f0 <code>/deathclock</code> \u2014 Token longevity prediction\n"
            "\U0001f525 <code>/launches</code> \u2014 New token launch feed\n"
            "\U0001f578\ufe0f <code>/cluster</code> \u2014 Wallet clustering & funding\n"
            "\U0001f451 <code>/pricing</code> \u2014 Upgrade your tier"
            f"{footer_links()}"
        ),
    ),
]


def local_ai_response(text: str, user: Any) -> str | None:
    """Tier 2: Match user intent against keyword patterns."""
    text_lower = text.lower().strip()
    for pattern, response in _INTENT_RULES:
        if pattern.search(text_lower):
            if len(text_lower) < 2:
                return None
            return response
    return None


__all__ = [
    "_INTENT_RULES",
    "_PRESENCE_ANSWER",
    "_SELF_DESCRIPTION_ANSWER",
    "_SELF_IMPROVEMENT_ANSWER",
    "local_ai_response",
]
