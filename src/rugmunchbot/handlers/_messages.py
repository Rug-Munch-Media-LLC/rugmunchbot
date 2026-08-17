#!/usr/bin/env python3
"""
RugMunchBot -- standardized user-facing messages (UX polish 2026-08-16).

Single source of truth for the bot's error + usage phrasing so every command
speaks the same language:

  * failure cards: what happened -> what to do (-> optional example)
  * usage hints:  Usage + a concrete example

No raw exception text is ever interpolated into these strings -- log it
server-side instead and show the user a friendly, actionable card.
"""

from __future__ import annotations

__all__ = [
    "ACTION_CHECK_ADDRESS",
    "ACTION_CONTACT_SUPPORT",
    "ACTION_TRY_AGAIN",
    "NOT_FOUND_MSG",
    "failure_card",
    "token_not_found_card",
    "usage_hint",
]

# ── Standardized action lines (what to do) ──────────────────────────────────
#: Generic retry guidance for transient failures (timeouts, backend down).
ACTION_TRY_AGAIN = "Please try again in a moment."
#: Guidance when the user's input is likely wrong (bad address, no pair).
ACTION_CHECK_ADDRESS = "Check the address and try again."
#: Fallback escalation line appended to repeated/unknown failures.
ACTION_CONTACT_SUPPORT = "If the issue persists, contact support."

#: Standard detail for "we looked but found nothing" states.
NOT_FOUND_MSG = "No data found for this token."


def failure_card(title: str, detail: str, action: str = ACTION_TRY_AGAIN) -> str:
    """Build a consistent, friendly failure card.

    Always "what happened" (title/detail) + "what to do" (action). Never
    includes raw exception text -- log it server-side instead.
    """
    return f"\u274c <b>{title}</b>\n\n{detail}\n\n{action}"


def token_not_found_card(token: str, cmd: str = "scan") -> str:
    """Build the standard token-not-found card with recovery steps."""
    return (
        "\u274c <b>Token Not Found</b>\n\n"
        f"No data found for <code>{token}</code>.\n\n"
        "\u2022 Check the address is correct\n"
        "\u2022 Ensure the token has a trading pair\n"
        f"\u2022 Try specifying the chain: /{cmd} <code>addr</code> solana"
    )


def usage_hint(
    cmd: str,
    arg_spec: str,
    example: str,
    extra: str = "",
    examples: tuple[str, ...] = (),
) -> str:
    """Build a consistent usage hint with concrete example(s).

    ``arg_spec`` is the placeholder shown inside the code tag (e.g.
    ``<address> [chain]``). ``example`` is the primary concrete value shown
    after ``/<cmd> ``; ``examples`` adds secondary ``Example:`` lines (e.g.
    chain variants) when the command accepts more than one shape.
    """
    parts = [
        f"<b>Usage:</b> <code>/{cmd} {arg_spec}</code>",
        f"Example: <code>/{cmd} {example}</code>",
    ]
    parts.extend(f"Example: <code>/{cmd} {ex}</code>" for ex in examples)
    if extra:
        parts.append("")
        parts.append(extra)
    return "\n\n".join(parts)
