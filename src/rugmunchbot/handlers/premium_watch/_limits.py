"""Watch quota per tier — the only place the numbers live.

Two tiers are named in the product: free and paid. The paid quota is one
number for every paid tier on purpose; the marketing table in
``config/_tiers.py`` still advertises 10/50 watches, and that copy is the
thing to change if the quota ever differs per tier — not this module.

Refusal is a message, never a silent no-op: the reader must learn the limit,
what they have, and the one command that lifts it.
"""

from __future__ import annotations

FREE_WATCHES = 2
PAID_WATCHES = 25
PAID_TIERS = frozenset({"scout", "hunter", "pro", "elite", "whale", "beta"})


def watch_limit(tier: str) -> int:
    """Quota for a tier name; anything unrecognised is treated as free."""
    return PAID_WATCHES if str(tier or "").strip().lower() in PAID_TIERS else FREE_WATCHES


def limit_refusal(used: int, limit: int) -> str:
    """The refusal card: what the limit is, what you have, how to lift it."""
    return (
        f"🚦 <b>Watchlist full</b> — {used}/{limit} on the free tier.\n\n"
        "Free: <b>2</b> tokens with live alerts.\n"
        "Pro: <b>25</b> tokens, plus cohort / bundle / authority deltas.\n\n"
        "Run /unwatch <code>address</code> to free a slot, or /pricing to upgrade.\n"
        "<i>Upgrade is not wired to Telegram Stars yet — payments land in the next release.</i>"
    )
