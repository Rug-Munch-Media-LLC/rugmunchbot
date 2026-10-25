"""Wire the moderation layer into the existing bot (no second bot, no polling).

Registration contract:
  * ``moderation_gate`` in handler group **-1** — it sees every update before
    the command/AI handlers; it no-ops for unconfigured chats.
  * commands in group **0** so they behave like every other command.
  * one repeating job posts scheduled messages; skipped when the application has
    no job queue (the harness and unit tests build apps without one).
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.moderation import (
    captcha,
    filters,
    gban,
    notes,
    quarantine,
    schedule,
    settings_cmd,
    state,
)
from rugmunchbot.moderation.commands import (
    cmd_appeal,
    cmd_modactions,
    cmd_modpardon,
    cmd_modrelease,
    cmd_modstatus,
)
from rugmunchbot.moderation.gate import moderation_gate
from rugmunchbot.moderation.raid_cmd import cmd_modraid
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.register")

COMMANDS: dict[str, Any] = {
    "modstatus": cmd_modstatus,
    "modactions": cmd_modactions,
    "modpardon": cmd_modpardon,
    "appeal": cmd_appeal,
    "save": notes.cmd_save,
    "get": notes.cmd_get,
    "notes": notes.cmd_notes,
    "clear": notes.cmd_clear,
    "filter": filters.cmd_filter,
    "filters": filters.cmd_filters,
    "stopfilter": filters.cmd_stopfilter,
    "gban": gban.cmd_gban,
    "ungban": gban.cmd_ungban,
    "gbans": gban.cmd_gbans,
    "schedule": schedule.cmd_schedule,
    "schedules": schedule.cmd_schedules,
    "unschedule": schedule.cmd_unschedule,
    "setwelcome": settings_cmd.cmd_setwelcome,
    "setgoodbye": settings_cmd.cmd_setgoodbye,
    "modconfig": settings_cmd.cmd_modconfig,
    "modraid": cmd_modraid,
    "modrelease": cmd_modrelease,
}

SCHEDULE_INTERVAL_S = 60


def recover_state(app: Any) -> int:
    """Restore containment state after a restart — never invent it.

    Quarantine holds, verified members and live captcha challenges are read
    back from the durable snapshot; each live challenge gets its timeout
    re-armed so a restart cannot orphan a muted member.
    """
    quarantine.recover()
    state.recover()
    return captcha.recover(app)


def register_moderation(app: Any) -> None:
    """Attach the gate, commands and the schedule tick to an existing Application."""
    try:
        recover_state(app)
    except Exception:
        logger.error("moderation state recovery failed", exc_info=True)
    from telegram import Update
    from telegram.ext import CommandHandler, TypeHandler

    app.add_handler(TypeHandler(Update, moderation_gate), group=-1)
    for name, callback in COMMANDS.items():
        app.add_handler(CommandHandler(name, callback), group=0)
    job_queue = getattr(app, "job_queue", None)
    if job_queue is not None:
        job_queue.run_repeating(
            schedule.tick, interval=SCHEDULE_INTERVAL_S, first=10, name="mod_schedule"
        )


__all__ = ["COMMANDS", "recover_state", "register_moderation"]
