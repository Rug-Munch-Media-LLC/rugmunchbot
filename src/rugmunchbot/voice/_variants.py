"""Phrasing variants per message key, and the register each key commits to.

Keys are stable identifiers (``cmd.watch_saved``), never the English text, so
copy can change without a caller changing. SERIOUS keys hold exactly one
variant — that is the structural guarantee that a loss alert never rotates
into something jauntier.
"""

from __future__ import annotations

from typing import Final

from rugmunchbot.voice._registers import Register

#: key -> (register, variants). One variant means "no rotation, on purpose".
VARIANTS: Final[dict[str, tuple[Register, tuple[str, ...]]]] = {
    # ── commands ────────────────────────────────────────────────────────
    "cmd.watch_saved": (
        Register.WITTY,
        (
            "On the list: {ticker}. Quiet until it isn't.",
            "Watching {ticker}. I'll shout if it moves.",
            "{ticker} added. No promises, just alerts.",
        ),
    ),
    "cmd.watch_removed": (
        Register.WITTY,
        (
            "Dropped {ticker}. Nothing moves quietly on my watch now.",
            "{ticker} off the list.",
        ),
    ),
    "cmd.watchlist_empty": (
        Register.WITTY,
        (
            "Empty. Add one with /watch {example}.",
            "No tickers on the list yet. /watch {example} starts it.",
        ),
    ),
    "cmd.portfolio_loading": (
        Register.NEUTRAL,
        ("Loading portfolio.", "Portfolio loading."),
    ),
    "plan.already_active": (
        Register.NEUTRAL,
        ("Free tier is already on.", "You're on the free tier already."),
    ),
    "market.noop": (
        Register.NEUTRAL,
        ("Nothing changed since the last scan.", "No change since the last scan."),
    ),
    # ── errors ──────────────────────────────────────────────────────────
    "err.action_failed": (
        Register.NEUTRAL,
        (
            "{action} didn't go through ({detail}).",
            "{action} failed ({detail}). Nothing was changed.",
        ),
    ),
    "err.rate_limit": (
        Register.NEUTRAL,
        (
            "Rate limited. Next slot in about a minute.",
            "Too many calls. Give it a minute.",
        ),
    ),
    "err.bad_days": (
        Register.NEUTRAL,
        ("Days has to be a whole number from 1 to 3650.",),
    ),
    "err.no_such_watch": (
        Register.NEUTRAL,
        ("No watch entry with that id.", "That id isn't on the list."),
    ),
    # ── scans ───────────────────────────────────────────────────────────
    "scan.progress": (
        Register.NEUTRAL,
        ("Scanning.", "Scanning now."),
    ),
    "scan.clean": (
        Register.WITTY,
        (
            "Nothing ugly in the top layers. That is not the same as safe.",
            "Clean at the surface. Read the holders before you size it.",
        ),
    ),
    "scan.caution": (
        Register.NEUTRAL,
        ("Caution. The flags are listed below — read them.",),
    ),
    "scan.danger": (
        Register.SERIOUS,
        ("Danger. Do not buy this on the current evidence.",),
    ),
    # ── alerts ──────────────────────────────────────────────────────────
    "alert.launch": (
        Register.NEUTRAL,
        ("New pair: {symbol}. Launched {age}.",),
    ),
    "alert.whale": (
        Register.WITTY,
        (
            "{wallets} wallets bought {size} of {ticker} in {window}. Same funding source: {venue}.",
            "{size} of {ticker} across {wallets} wallets. One funding wallet behind all of them.",
        ),
    ),
    "alert.rug": (
        Register.SERIOUS,
        ("LP pulled {when}. Risk {score}/100. {holders} holders still in.",),
    ),
    "alert.security": (
        Register.SERIOUS,
        ("Security flag on {target}: {detail}.",),
    ),
    "loss.disclosure": (
        Register.SERIOUS,
        ("Position {ticker} is down {pct}. The stop was {stop}.",),
    ),
    # ── onboarding ──────────────────────────────────────────────────────
    "onboard.welcome": (
        Register.WITTY,
        (
            "You're in. /scan {example} to check a token before it checks you.",
            "Welcome. Start with /scan {example} — cheaper than learning the hard way.",
        ),
    ),
    "onboard.help_hint": (
        Register.NEUTRAL,
        ("Full command list: /help. Pricing: /plans.",),
    ),
    # ── moderation ──────────────────────────────────────────────────────
    "mod.notice": (
        Register.SERIOUS,
        ("{action} for {reason}. Case {case_id}.",),
    ),
    "mod.appeal": (
        Register.SERIOUS,
        ("Appeal in {appeal_channel} with case {case_id}.",),
    ),
}

#: Keys whose register is SERIOUS rotate never, by construction.
assert all(
    len(v) == 1 for reg, v in VARIANTS.values() if reg is Register.SERIOUS
), "SERIOUS keys must hold exactly one variant"
