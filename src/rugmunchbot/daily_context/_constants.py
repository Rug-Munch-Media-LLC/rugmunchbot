"""Daily market awareness constants — endpoints, budgets, cache keys.

Extracted verbatim from the former single-module ``daily_context.py``.
"""

from __future__ import annotations

# ── Backend endpoints (all own-data; the backend caches each of these) ──
OVERVIEW_PATH = "/api/v1/market/overview"  # total mcap, fear/greed, dominance
MACRO_PATH = "/api/v1/market/macro"  # BTC/ETH spot prices + 24h change
NEWS_PATH = "/api/v1/news/trending"  # today's top stories
TRENDING_PATH = "/api/v1/market/trending"  # trending tokens
LAUNCHES_PATH = "/api/v1/launches/recent"  # just-deployed tokens

# ── Budgets ─────────────────────────────────────────────────────────────
CACHE_KEY = "rmi_bot:daily_context:v1"
CACHE_TTL = 300  # 5 minutes
FETCH_TIMEOUT = 5.0  # per-endpoint budget; all fetch in parallel
NEWS_LIMIT = 5  # fetch a few, render top 3
TRENDING_LIMIT = 10  # route default is 8; render up to 10
LAUNCH_LIMIT = 5
HEADLINE_MAX_CHARS = 110  # per-headline truncation inside the context block
CONTEXT_MAX_CHARS = 1400  # hard cap on the LLM-facing context block
