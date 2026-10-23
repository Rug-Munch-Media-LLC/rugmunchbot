"""``/buybot`` argument grammar and application — Telegram-free, unit-testable.

Grammar is deliberately tiny: one option per message, values validated by the
models (which are the same ones ``/setup`` writes), so there is exactly one
place where an address, a preset or an emoji is accepted or rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rugmunchbot.buybot.models import BuybotConfig

__all__ = ["ALIASES", "SUBCOMMANDS", "Intent", "apply_intent", "parse"]

SUBCOMMANDS: tuple[str, ...] = (
    "token",
    "presets",
    "emoji",
    "min",
    "fields",
    "buttons",
    "dedupe",
    "cap",
)
ALIASES = {
    "mint": "token",
    "amounts": "presets",
    "label": "emoji",
    "min_usd": "min",
    "field": "fields",
    "button": "buttons",
}
USAGE = {
    "token": "/buybot token <chain> <address> [symbol]",
    "presets": "/buybot presets 0.1,0.5,1",
    "emoji": "/buybot emoji <emoji> [label]",
    "min": "/buybot min <usd>",
    "fields": "/buybot fields amount,usd,price,mcap,liquidity,risk",
    "buttons": "/buybot buttons chart,scan,holders",
    "dedupe": "/buybot dedupe <seconds>",
    "cap": "/buybot cap <cards per minute>",
}


@dataclass(frozen=True)
class Intent:
    """One parsed option, still unvalidated."""

    field: str
    args: tuple[str, ...]


def parse(argv: list[str] | tuple[str, ...]) -> Intent | None:
    """Parse ``/buybot`` argv into an intent; ``None`` means "show status"."""
    parts = [str(part).strip() for part in argv if str(part).strip()]
    if not parts:
        return None
    head = parts[0].lower()
    head = ALIASES.get(head, head)
    if head not in SUBCOMMANDS:
        raise ValueError(f"unknown option '{head}' — try {', '.join(SUBCOMMANDS)}")
    if len(parts) == 1:
        raise ValueError(USAGE[head])
    return Intent(head, tuple(parts[1:]))


def _derived_symbol(address: str) -> str:
    """Best-effort ticker from an address — alphanumeric only, never a guess at data."""
    guess = "".join(ch for ch in address[:6].upper() if ch.isascii() and ch.isalnum())
    return guess or "TOKEN"


def _updates(config: BuybotConfig, intent: Intent) -> dict[str, Any]:
    """Translate an intent into a partial config document."""
    args = intent.args
    if intent.field == "token":
        if len(args) < 2:
            raise ValueError(USAGE["token"])
        symbol = args[2] if len(args) > 2 else _derived_symbol(args[1])
        chain = args[0].lower()
        return {
            "token": {"chain": chain, "address": args[1], "symbol": symbol, "name": symbol},
        }
    if intent.field == "presets":
        amounts = config.amounts.model_dump()
        amounts["buy"] = args[0]
        return {"amounts": amounts}
    if intent.field == "emoji":
        emojis = config.emojis.model_dump()
        emojis["buy"]["emoji"] = args[0]
        if len(args) > 1:
            emojis["buy"]["label"] = " ".join(args[1:])
        return {"emojis": emojis}
    if intent.field == "min":
        return {"min_buy_usd": args[0]}
    if intent.field in {"fields", "buttons"}:
        return {intent.field: args[0]}
    key = "dedupe_window_s" if intent.field == "dedupe" else "max_cards_per_min"
    return {key: args[0]}


def apply_intent(config: BuybotConfig, intent: Intent) -> BuybotConfig:
    """Return a validated copy of *config* with *intent* applied.

    Raises :class:`ValueError` (pydantic's ``ValidationError`` included) with a
    message that is safe to show the administrator.
    """
    document = config.model_dump()
    document.update(_updates(config, intent))
    document["chat_id"] = config.chat_id
    return BuybotConfig.model_validate(document)
