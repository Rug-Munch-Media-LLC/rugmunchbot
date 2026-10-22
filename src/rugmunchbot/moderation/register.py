"""Wire the moderation layer into the existing bot (no second bot, no polling).

Registration contract:
  * ``moderation_gate`` in handler group **-1** — it sees every update before
    the command/AI handlers; it no-ops for unconfigured chats.
  * commands in group **0** so they behave like every other command.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.moderation.commands import (
    cmd_appeal,
    cmd_modactions,
    cmd_modpardon,
    cmd_modstatus,
)
from rugmunchbot.moderation.gate import moderation_gate


def register_moderation(app: Any) -> None:
    """Attach the gate + commands to an existing PTB Application."""
    from telegram import Update
    from telegram.ext import CommandHandler, TypeHandler

    app.add_handler(TypeHandler(Update, moderation_gate), group=-1)
    app.add_handler(CommandHandler("modstatus", cmd_modstatus), group=0)
    app.add_handler(CommandHandler("modactions", cmd_modactions), group=0)
    app.add_handler(CommandHandler("modpardon", cmd_modpardon), group=0)
    app.add_handler(CommandHandler("appeal", cmd_appeal), group=0)


__all__ = ["register_moderation"]
