#!/usr/bin/env python3
"""Scam School educational content topics."""

from __future__ import annotations

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
