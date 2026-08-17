#!/usr/bin/env python3
"""
RugMunchBot — State management (spam protection, rate limits, caches).

Keeps all in-memory state in one place for easy visibility.
"""

from __future__ import annotations

import time
from typing import Any

from cachetools import TTLCache

from app.domains.telegram.rugmunchbot.config import OWNER_IDS
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client

logger = get_logger("rmi_bot")

# ══════════════════════════════════════════════
# SOL PRICE CACHE
# ══════════════════════════════════════════════
_sol_price: float = 150.0  # fallback
_sol_price_ts: float = 0


async def get_sol_price() -> float:
    """Fetch current SOL/USD price from Jupiter API (free, no auth needed)."""
    global _sol_price, _sol_price_ts
    import httpx

    now = time.time()
    if now - _sol_price_ts < 300:  # cache for 5 minutes
        return _sol_price
    try:
        client = get_shared_client(5)
        r = await client.get("https://api.jup.ag/price/v2?ids=So11111111111111111111111111111111111111112")
        if r.status_code == 200:
            data = r.json()
            price_str = data.get("data", {}).get("So11111111111111111111111111111111111111112", {}).get("price")
            if price_str:
                _sol_price = float(price_str)
                _sol_price_ts = now
                return _sol_price
        # Jupiter unreachable/blocked from some hosts — CoinGecko fallback
        r = await client.get("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd")
        if r.status_code == 200:
            price = r.json().get("solana", {}).get("usd")
            if price:
                _sol_price = float(price)
                _sol_price_ts = now
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Failed to fetch SOL price: {e}")
    return _sol_price


# ══════════════════════════════════════════════
# SPAM PROTECTION
# ══════════════════════════════════════════════
_user_last_action: TTLCache[int, float] = TTLCache(maxsize=10000, ttl=60)
SPAM_COOLDOWN = 3


def check_spam(user_id: int) -> bool:
    """Return True if the user is spamming (rate-limited)."""
    now = time.time()
    last = _user_last_action.get(user_id, 0)
    if now - last < SPAM_COOLDOWN and not is_owner(user_id):
        return True
    _user_last_action[user_id] = now
    return False


def is_owner(uid: int) -> bool:
    """Check if a user ID is a bot owner."""
    return uid in OWNER_IDS


# ── LLM general-synthesis cooldown (tier 1b chat) ──
# Limits ungrounded LLM calls to ~1 per user per 10s so a flood of free-text
# messages cannot hammer the backend LLM. Grounded RAG+LLM answers (tier 1)
# are NOT gated here — they are rarer and more valuable.
_llm_recent: TTLCache[int, bool] = TTLCache(maxsize=10000, ttl=10)


def should_skip_llm(user_id: int) -> bool:
    """Return True if this user called the general LLM within the cooldown.

    Marks the user on first call; the TTL cache expires the mark after 10s.
    """
    if is_owner(user_id):
        return False
    if user_id in _llm_recent:
        return True
    _llm_recent[user_id] = True
    return False


# ── Alert rate limiter (max 1 DM per user per 5 min) ──
_alert_last_sent: dict[int, float] = {}
ALERT_RATE_LIMIT_SEC = 300


def check_alert_rate_limit(user_id: int) -> bool:
    """Return True if user can receive another alert (rate-limited to 1 per 5 min)."""
    now = time.time()
    last = _alert_last_sent.get(user_id, 0)
    if now - last < ALERT_RATE_LIMIT_SEC:
        return False
    _alert_last_sent[user_id] = now
    return True


# ── Pending deep scan payments (token → details) ──
_pending_deep_scans: dict[str, dict[str, Any]] = {}

# ── Pending monitor payments (token → details) ──
# Mirrors ``_pending_deep_scans``: the callback/command stores the pending
# monitor under a random token and sends a Stars invoice with payload
# ``mon_{token}``; ``successful_payment`` pops it on confirmation.
_pending_monitors: dict[str, dict[str, Any]] = {}
