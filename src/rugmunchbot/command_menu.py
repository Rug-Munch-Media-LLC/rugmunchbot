"""Advertised command menu — the 20 commands in the BotFather menu.

Data only (no Telegram API, no bot import) so both the registration wiring
(``bot/_commands.py``) and the rendered help (``handlers/_help_text.py``) read
the SAME list without a circular import. Progressive disclosure: everything
else stays registered and lives under ``/more`` (``command_catalog.py``).
"""

from __future__ import annotations

#: Hard ceiling for the advertised BotFather menu. 20 is the most a human
#: scans in one screen; the rest live under /more.
MAX_ADVERTISED = 20

#: (group header, [(command, one-line purpose)]). Groups are presentation
#: only — Telegram shows a flat list — but they keep /help readable.
ADVERTISED_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Start here",
        (
            ("start", "Welcome & 5-command quickstart"),
            ("scan", "Scan a token for scams & risks"),
            ("desk", "Latest write-ups from the desk"),
            ("plans", "Free vs Pro — stated honestly"),
            ("help", "How the bot works"),
        ),
    ),
    (
        "Scan & safety",
        (
            ("deepscan", "Deep scan — full forensic audit"),
            ("wallet", "Wallet forensics & analysis"),
            ("holders", "Holder distribution analysis"),
            ("trace", "Cross-chain fund trace"),
        ),
    ),
    (
        "Markets",
        (
            ("price", "Token price by address"),
            ("chart", "Chart & technical analysis"),
            ("trending", "Hot tokens right now"),
            ("market", "Market overview & trends"),
        ),
    ),
    (
        "Intel",
        (
            ("news", "Latest crypto news"),
            ("research", "Deep research report on a token"),
            ("alerts", "Manage price alerts"),
            ("portfolio", "Your portfolio: value, P&L, 24h"),
        ),
    ),
    (
        "Account",
        (
            ("account", "Your account, tier & usage"),
            ("report", "Report a scam / rug"),
            ("more", "All commands, grouped by category"),
        ),
    ),
)

#: Advertised command names, in menu order (duplicates preserved for the
#: guard to detect; do not dedupe here).
ADVERTISED_COMMANDS: tuple[str, ...] = tuple(
    command for _header, commands in ADVERTISED_SECTIONS for command, _desc in commands
)


#: The group-admin / moderation surface. Registered for every chat, but not
#: public: a DM user has no use for /gban, and advertising a chat-admin command
#: in the public catalogue is a lie (a non-admin gets "⛔ Chat admins only").
#: /help renders this as a one-line block for group admins, and the surface
#: test treats it as documented (so it is never "dead but unexplained").
GROUP_ADMIN_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Moderation",
        (
            ("modstatus", "This chat's moderation policy"),
            ("modraid", "Show, engage or lift a raid lockdown"),
            ("modrelease", "Release a quarantined member"),
            ("modactions", "Recent audited moderation actions"),
            ("modpardon", "Pardon an audited action"),
            ("modrules", "The rules this chat enforces"),
            ("warn", "Warn a member"),
            ("mute", "Mute a member"),
            ("ban", "Ban a member"),
            ("trust", "Mark a member trusted"),
            ("untrust", "Remove trusted status"),
            ("appeal", "Appeal a moderation action"),
            ("filter", "Add a word/domain filter"),
            ("filters", "List this chat's filters"),
            ("stopfilter", "Remove a filter"),
            ("gban", "Global ban (network-wide)"),
            ("ungban", "Lift a global ban"),
            ("gbans", "List global bans"),
        ),
    ),
    (
        "Chat setup",
        (
            ("setup", "Token, amounts, emoji & config for this chat"),
            ("buybot", "Configure the buy popup for this chat"),
            ("buybot_on", "Start posting buy cards here"),
            ("buybot_off", "Stop posting buy cards"),
            ("buybot_preview", "Preview the buy card"),
            ("buybot_template", "Show the buy-card template"),
            ("setwelcome", "Set the welcome message"),
            ("setgoodbye", "Set the goodbye message"),
            ("modconfig", "Show moderation config"),
            ("save", "Save a note"),
            ("get", "Read a note"),
            ("notes", "List notes"),
            ("clear", "Delete a note"),
            ("schedule", "Schedule a recurring post"),
            ("schedules", "List scheduled posts"),
            ("unschedule", "Cancel a scheduled post"),
        ),
    ),
)

#: Every documented group-admin command (flattened, one source).
GROUP_ADMIN_COMMANDS: frozenset[str] = frozenset(
    command for _h, commands in GROUP_ADMIN_SECTIONS for command, _desc in commands
)


def advertised_set() -> frozenset[str]:
    """The advertised command names as a set (menu source of truth)."""
    return frozenset(ADVERTISED_COMMANDS)


def menu_rows() -> tuple[tuple[str, str], ...]:
    """Flattened ``(command, description)`` rows in advertised order."""
    return tuple(
        (command, desc) for _h, commands in ADVERTISED_SECTIONS for command, desc in commands
    )


__all__ = [
    "ADVERTISED_COMMANDS",
    "ADVERTISED_SECTIONS",
    "GROUP_ADMIN_COMMANDS",
    "GROUP_ADMIN_SECTIONS",
    "MAX_ADVERTISED",
    "advertised_set",
    "menu_rows",
]
