"""Static card texts — welcome, about, FAQ, paywall, social proof."""

from __future__ import annotations

from rugmunchbot import db
from rugmunchbot.config import (
    TIERS,
)
from rugmunchbot.formatters._base._links import footer_links
from rugmunchbot.formatters._base._primitives import BETA_BANNER


def social_proof_text() -> str:
    """Evidence-first "why trust us" block.

    Only figures verified in the fleet docs: 17 chains / 359M+ on-chain
    rows (lite-chain-indexer README) and 200+ tools (rmi-backend
    ARCHITECTURE.md). No invented user/scan counters and no DB access, so
    this stays a pure, deterministic string formatter.
    """
    return (
        "\U0001f3d7\ufe0f <b>Why trust us</b>\n"
        "\U0001f517 17 chains indexed \u00b7 359M+ on-chain rows\n"
        "\U0001f6e0\ufe0f 200+ tools \u00b7 24/7 AI \u2014 free during public beta"
    )


def about_text() -> str:
    """Backstory block — who is behind Rug Munch Bot.

    Used by /start, /help and /faq so every surface carries the same
    company line. HTML-safe for Telegram: only ``<b>`` tags.
    """
    return (
        "\U0001f916 <b>About</b>\n"
        "Rug Munch Bot is created by the team behind "
        "<b>RMI (Rug Munch Intelligence)</b> \u2014 a division of "
        "<b>Rug Munch Media LLC</b>, a Wyoming-based company \u2014 to "
        "protect retail investors from crypto scams with on-chain "
        "intelligence, scanner forensics, and AI analysis.\n\n"
        "Developed by @cryptorugmunch and the team at Rug Munch Media LLC."
    )


def faq_text() -> str:
    """Lightweight FAQ — plain-language answers to common questions.

    Pure string formatter (no network, no DB) so /faq always renders and is
    unit-testable. HTML-safe for Telegram: only ``<b>`` / ``<code>`` tags and
    ``&amp;`` escaping — no raw markdown.
    """
    return (
        "\U0001f4d6 <b>Rug Munch Bot - FAQ</b>\n\n"
        "\U0001f916 <b>What is Rug Munch Bot?</b>\n"
        "An AI scam-detection copilot: paste any token address or wallet and "
        "get instant audits, forensics and risk scores.\n\n"
        "\U0001f50d <b>What can you scan?</b>\n"
        "Tokens on EVM chains and Solana, wallets, deployers, LP health, "
        "honeypots, MEV risk, hype scores and more. Try /scan "
        "<code>address</code> or /wallet <code>address</code>.\n\n"
        "\U0001f4b0 <b>Is it free?</b>\n"
        "Yes! We\u2019re in <b>FREE PUBLIC BETA</b> \u2014 every premium "
        "feature is unlocked while we test. Paid plans (Scout/Hunter/Pro) "
        "come later.\n\n"
        "\U0001f3db\ufe0f <b>What is RMI?</b>\n"
        "RMI (Rug Munch Intelligence) is the team behind the bot, owned by "
        "<b>Rug Munch Media LLC</b>, a Wyoming-based company. We build "
        "on-chain intelligence tools that protect retail investors.\n\n"
        "\U0001f3af <b>How accurate are the scans?</b>\n"
        "Our AEGIS engine runs 9 forensics modules over 17 indexed chains "
        "(359M+ on-chain rows) with 200+ tools. No tool is 100% \u2014 "
        "always DYOR, and pair a scan with the charts on rugmunch.io.\n\n"
        "\u26a0\ufe0f <b>See a scam?</b>\n"
        "Report it with /report <code>address</code> \u2014 it keeps "
        "everyone safer.\n\n"
        "\U0001f4d6 More: /help for the full command guide"
    )


def welcome_card_text() -> str:
    """Modern capability overview — brand line, "what I can do" list, CTA.

    Shown by /start, the main menu (``menu_main``) and the welcome card.
    Every capability bullet maps to real bot commands (see ``cmd_help``
    for the full reference). HTML-safe for Telegram: only ``<b>`` /
    ``<i>`` / ``<code>`` tags and ``&amp;`` escaping — no raw markdown.
    """
    return (
        f"\U0001f6e1\ufe0f <b>Rug Munch Bot</b> \u2014 by @cryptorugmunch and "
        f"the team at Rug Munch Media LLC\n"
        f"<i>The scam-detection copilot for crypto</i>\n\n"
        f"{social_proof_text()}\n\n"
        f"\U0001f310 <b>Full RMI app \u2014 rugmunch.io</b>\n"
        f"The complete Rug Munch Intelligence platform lives on the web: "
        f'<a href="https://rugmunch.io">rugmunch.io</a> \u2014 '
        f"token scanner, AI chat, market intelligence and more, all in one "
        f"dashboard.\n"
        f"\U0001f5fa\ufe0f <b>RugMaps</b> \u2014 interactive token &amp; wallet "
        f"relationship maps\n"
        f"\U0001f4c8 <b>RugCharts</b> \u2014 live charts, liquidity &amp; "
        f"technical signals\n"
        f"\U0001f50d <b>Scanner</b> \u2014 the same AEGIS forensics as this bot\n"
        f"\U0001f916 <b>AI Chat</b> \u2014 deep RAG answers with on-chain "
        f"intelligence\n"
        f"\U0001f4ca <b>Market Intelligence</b> \u2014 news, sentiment &amp; "
        f"whale flows\n\n"
        f"<b>What I can do</b>\n"
        f"\U0001f50d <b>Scan</b> \u2014 instant token audits &amp; AEGIS forensics\n"
        f"\U0001f45b <b>Wallet</b> \u2014 forensics, portfolio &amp; funding analysis\n"
        f"\U0001f575\ufe0f <b>Trace</b> \u2014 cross-chain wallet tracing (Arkham-style)\n"
        f"\U0001f4b0 <b>Price</b> \u2014 live prices, charts &amp; market snapshots\n"
        f"\U0001f4ca <b>Technical</b> \u2014 RSI, MACD, support &amp; resistance\n"
        f"\U0001f4f0 <b>News</b> \u2014 live crypto news + RAG AI chat\n"
        f"\U0001f514 <b>Alerts</b> \u2014 price, watchlist &amp; whale alerts\n"
        f"\U0001f4da <b>Scam School</b> \u2014 learn the plays &amp; KOL/scammer tracking\n\n"
        f"{about_text()}\n\n"
        f"{BETA_BANNER}\n\n"
        f"\U0001f4cc Try <code>/scan</code> <code>address</code> or "
        f"<code>/chat</code> <code>question</code>\n"
        f"\U0001f4cc New here? <code>/faq</code> \u2014 how it works\n"
        f"\U0001f4cc Pick a section below \U0001f447\n\n"
        f"\U0001f6e0\ufe0f <b>Built in Public</b>\n"
        f"Every line of code ships open source \u2014 "
        f'<a href="https://codeberg.org/cryptorugmunch">Codeberg</a>'
        f" \u00b7 "
        f'<a href="https://git.rugmunch.io/RugMunchMedia">Forgejo</a>'
        f" \u00b7 "
        f'<a href="https://gitlab.com/cryptorugmunch">GitLab</a>'
        f" \u00b7 "
        f'<a href="https://git.cryptorugmunch.com">Radicle</a>\n'
        f"{footer_links()}"
    )


def paywall_text(user_id: int, action: str = "scan") -> str:
    tier = db.get_user_tier(user_id)
    scans, ai = db.get_weekly_usage(user_id)
    user = db.get_or_create_user(user_id)
    tc = TIERS.get(tier, TIERS["free"])
    limit = tc.get("scans_per_week", 25) if action == "scan" else tc.get("ai_msgs_per_week", 15)
    used = scans if action == "scan" else ai
    bonus = user.get("bonus_scans", 0) if action == "scan" else user.get("bonus_ai_msgs", 0)
    # Public beta: hitting the (boosted) weekly quota is a REAL limit, but
    # tell free users it's a beta allocation that resets — not a paywall.
    beta_note = ""
    if tier == "free":
        beta_note = (
            "\n\U0001f680 <b>Beta:</b> your weekly limit is boosted for the "
            "public beta \u2014 resets Monday 00:00 UTC.\n"
        )
    return (
        f"\u26a1 <b>Weekly {action.title()} Limit Reached</b>\n\n"
        f"\U0001f4ca <b>{tc['name']} Tier:</b> {used}/{limit} used this week\n"
        f"\U0001f381 <b>Bonus Balance:</b> {bonus} extra\n"
        f"{beta_note}"
        f"\n<b>Unlock more power:</b>\n\n"
        f"\U0001f6f0\ufe0f <b>Monitor (250\u2605/30d)</b> \u2014 live LP/whale + tax/ownership/mint change alerts\n"
        f"\U0001f52c <b>Deep Scan (250\u2605)</b> \u2014 full forensics on demand\n"
        f"\U0001f4b0 <b>Core /scan + chat stay free.</b>\n\n"
        f"Or buy one-time top-ups - <b>never expire!</b>\n"
        f"\U0001f50b 10 scans = \u2b5075 | 50 scans = \u2b50300 | 100 scans = \u2b50500"
    )
