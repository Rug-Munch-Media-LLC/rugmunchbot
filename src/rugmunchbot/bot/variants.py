"""Deterministic reply-variant picker.

Same (chat_id, message_id, situation) always yields the same variant, so a
reply is replayable from its own identifiers; different messages/users land
on different phrasings. Numbers are never generated here - they arrive as
fields and are formatted through unchanged.
"""
from __future__ import annotations

import hashlib

POOLS: dict[str, tuple[str, ...]] = {
    "scan.header": (
        "Scanning {subject}…",
        "Looking at {subject} now…",
        "Pulling {subject} apart…",
    ),
    "scan.empty": (
        "Send a token address after the command.",
        "That needs an address — paste the contract.",
        "No address given. Paste a token contract.",
    ),
    "scan.bad_address": (
        "That is not a valid address ({addr}).",
        "Address rejected: {addr}.",
        "{addr} does not parse as a token address.",
    ),
    "risk.verdict": (
        "Verdict: {verdict} (score {score}).",
        "Score {score} — {verdict}.",
        "Scored {score}: {verdict}.",
    ),
    "portfolio.empty": (
        "No wallets tracked yet. Add one with /watch <address>.",
        "Nothing tracked. Use /watch <address> to start.",
        "Wallet list is empty — /watch <address> adds one.",
    ),
    "rate.limited": (
        "Slow down — {retry} s.",
        "Rate limit hit, retry in {retry}s.",
        "Too fast. Try again in {retry} s.",
    ),
    "notfound.symbol": (
        "No market data for {symbol}.",
        "{symbol} has no data right now.",
        "Could not find {symbol}.",
    ),
    "alert.set": (
        "Alert set at {target}.",
        "Watching for {target}.",
        "Alert armed: {target}.",
    ),
    "generic.error": (
        "That failed: {detail}",
        "Error: {detail}",
        "Could not finish — {detail}",
    ),
    "upgrade.tier": (
        "{feature} needs {tier}.",
        "{feature} is a {tier} feature.",
        "Unlock {feature} on {tier}.",
    ),
}


def render_variant(situation: str, chat_id: int | None = None,
                   message_id: int | None = None, **fields: object) -> str:
    """Pick one phrase for `situation`, deterministically, and fill it."""
    pool = POOLS[situation]
    seed = f"{chat_id}:{message_id}:{situation}".encode()
    index = int.from_bytes(hashlib.blake2b(seed, digest_size=8).digest(), "big") % len(pool)
    return pool[index].format(**fields)
