"""Webhook constants — event catalog and backend event mapping.

Extracted verbatim from the former single-module ``handlers/webhook.py``.
"""

from __future__ import annotations

# Webhook event types users can subscribe to
WEBHOOK_EVENTS = {
    "price_change": "📊 Price change notifications",
    "whale_move": "🐋 Whale movement alerts",
    "token_deployed": "🆕 New token deployment",
    "liquidity_change": "💧 Liquidity change alerts",
    "first_swap": "🔄 First swap on new token",
    "wallet_activity": "👛 Wallet activity alerts",
}

WEBHOOK_EVENT_EMOJIS = {
    "price_change": "📊",
    "whale_move": "🐋",
    "token_deployed": "🆕",
    "liquidity_change": "💧",
    "first_swap": "🔄",
    "wallet_activity": "👛",
}

# Map bot event names to backend /api/v1/scanner/webhooks event types
BACKEND_EVENT_MAP = {
    "price_change": "token.price_change",
    "whale_move": "token.whale_move",
    "token_deployed": "token.deployed",
    "liquidity_change": "token.liquidity_change",
    "first_swap": "token.first_swap",
    "wallet_activity": "wallet.activity",
}
