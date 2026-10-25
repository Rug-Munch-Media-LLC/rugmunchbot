"""Watch quota per tier — the enforcement of the pricing table's watchlist claim.

``config/_tiers.py`` is the promise ("Watchlist (10 tokens)", "Watchlist (50
tokens)", "Unlimited watchlists"); this module is what the user actually gets.
The two must not drift, so ``tests/test_premium_entitlements.py`` parses that
copy and asserts it equals :func:`watch_limit` for every tier that names a
number — the copy is the source of truth, this file follows it.

Refusal is a message, never a silent no-op: the reader must learn the limit,
what they have, and the one command that lifts it.
"""

from __future__ import annotations

from rugmunchbot.premium.gating import rank_of

#: A tier with no watchlist ceiling. Negative so ``used < limit`` is always true
#: and no caller has to special-case ``None``.
UNLIMITED = -1

#: Watchlist slots per tier, mirroring the pricing copy. ``hunter`` names 50 and
#: ``pro`` says "Unlimited", so those are the values enforced here.
WATCH_LIMITS: dict[str, int] = {
    "free": 2,
    "scout": 10,
    "hunter": 50,
    "pro": UNLIMITED,
    "elite": UNLIMITED,
    "whale": UNLIMITED,
    "beta": UNLIMITED,
}

#: The tier that lifts a free user, named in the refusal so the path is obvious.
UPGRADE_TIER = "scout"

#: Back-compat aliases for ``premium_watch.__init__``. ``PAID_WATCHES`` is the
#: highest *finite* paid quota — pro is unlimited and has no number to report.
FREE_WATCHES = WATCH_LIMITS["free"]
PAID_WATCHES = WATCH_LIMITS["hunter"]
PAID_TIERS = frozenset({"scout", "hunter", "pro", "elite", "whale", "beta"})


def watch_limit(tier: str) -> int:
    """Quota for a tier name; anything unrecognised is treated as free.

    Rank decides the fallback, not a membership check, so a legacy billing name
    (``elite``) or a future paid tier cannot silently drop to the free quota.
    """
    name = str(tier or "").strip().lower()
    if name in WATCH_LIMITS:
        return WATCH_LIMITS[name]
    return UNLIMITED if rank_of(name) >= rank_of("pro") else WATCH_LIMITS["free"]


def has_room(used: int, tier: str) -> bool:
    """True when one more watch fits — an unlimited tier always has room."""
    limit = watch_limit(tier)
    return limit < 0 or used < limit


def limit_text(tier: str) -> str:
    """The quota as the user reads it: a number, or ``∞`` when uncapped."""
    limit = watch_limit(tier)
    return "∞" if limit < 0 else str(limit)


def limit_refusal(used: int, limit: int) -> str:
    """The refusal card: what the limit is, what you have, how to lift it."""
    return (
        f"🚦 <b>Watchlist full</b> — {used}/{limit} on the free tier.\n\n"
        f"Free: <b>{WATCH_LIMITS['free']}</b> tokens with live alerts.\n"
        f"{UPGRADE_TIER.title()}: <b>{WATCH_LIMITS[UPGRADE_TIER]}</b> tokens, "
        f"Hunter: <b>{WATCH_LIMITS['hunter']}</b>, Pro: <b>unlimited</b> — plus "
        "cohort / bundle / authority deltas.\n\n"
        "Run /unwatch <code>address</code> to free a slot, or /pricing to upgrade.\n"
        "<i>Upgrade is not wired to Telegram Stars yet — payments land in the next release.</i>"
    )
