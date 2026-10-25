"""Unknown-command fallback — a typo must not look like a dead bot.

Registered in handler group 1, AFTER every real command handler. PTB runs one
handler per group per update, so a known command is already served in group 0
and this handler only speaks when nothing else did. It re-checks the registry
anyway (cheap, exact) so it can never answer for a command that worked.

2026-10-25: before this, ``/scna`` or ``/hlwhales2`` was silently swallowed —
the user got no reply at all and assumed the bot was down.
"""

from __future__ import annotations

from typing import Any

HINT = (
    "\U0001f914 <b>/{name}</b> is not a command.\n\n"
    "/help \u2014 the guided tour\n"
    "/more \u2014 every command, grouped\n"
    "Or paste a token address and I will scan it."
)


async def cmd_unknown(update: Any, context: Any) -> None:
    """Answer only when the command name is not registered anywhere."""
    message = update.effective_message
    text = (getattr(message, "text", None) or "").strip()
    if not text.startswith("/"):
        return
    name = text[1:].split()[0].split("@")[0].lower()
    if not name or name in _registered(context):
        return
    await message.reply_text(HINT.format(name=name), parse_mode="HTML")


def _registered(context: Any) -> set[str]:
    """Every command name the live application registered (all groups)."""
    from telegram.ext import CommandHandler

    app = getattr(context, "application", None)
    names: set[str] = set()
    for group in getattr(app, "handlers", {}).values():
        for handler in group:
            if isinstance(handler, CommandHandler):
                names.update(handler.commands)
    return names


__all__ = ["cmd_unknown"]
