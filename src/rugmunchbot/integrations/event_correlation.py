"""News-to-price event correlation — OSS fail-open no-op.

The live bot correlates news events with price moves via a private scanner
engine (``app.domains.scanner.engines.event_correlation``) that reads the
backend's Postgres news store + ClickHouse price oracle. OSS ships no engine:
``correlate_news_with_price`` returns an empty result set so the caller
(``commands/events.py``) fails open with "no correlatable news events".
Self-hosters can implement the same module name against their own backend.

# TODO(port): wire an optional adapter here once an operator backend exposes
# the same contract — token/chain -> {"events": [...], "summary": str}.
"""

from __future__ import annotations

from typing import Any


async def correlate_news_with_price(
    token_address: str,
    chain: str,
    lookback_hours: int = 24,
) -> dict[str, Any]:
    """No-op: no news/price correlation engine in the OSS build.

    Signature matches the live private engine
    (``app.domains.scanner.engines.event_correlation.correlate_news_with_price``)
    so ``commands/events.py`` keeps working unchanged. Fails open: returns an
    empty ``events`` list and a neutral ``summary``; callers render the
    "no correlatable news events" path.

    [REVIEW] The porting brief described the internal pipeline inputs
    (``news_items``, ``price_data``), but the PUBLIC API the live module and
    the already-ported OSS caller use is ``(token_address, chain,
    lookback_hours)`` returning ``{"events", "summary", ...}`` — this adapter
    implements the public contract so the caller resolves.
    """
    return {
        "events": [],
        "summary": "No news event correlation engine configured in this build.",
        "lookback_hours": lookback_hours,
        "token_address": token_address,
        "chain": chain,
    }


__all__ = ["correlate_news_with_price"]
