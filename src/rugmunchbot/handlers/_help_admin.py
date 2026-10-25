"""The `/help` block for the chat-admin surface, generated from the registry.

Kept out of ``_help_text.py`` so that module keeps shrinking: the moderation
commands are registered for every chat but are only meaningful to a group
admin, so they live behind one compact, generated block instead of a
hand-written list that could drift.
"""

from __future__ import annotations

from rugmunchbot.command_menu import GROUP_ADMIN_SECTIONS


def group_admin_block() -> str:
    """Render the two-line 'Group admins only' block for /help."""
    lines = []
    for header, commands in GROUP_ADMIN_SECTIONS:
        names = " ".join(f"/{cmd}" for cmd, _desc in commands)
        lines.append(f"\u2022 <b>{header}</b>: {names}")
    return (
        "\n\U0001f6e1\ufe0f <b>Group admins only</b> "
        "(chat admins; invisible in DMs)\n" + "\n".join(lines)
    )


__all__ = ["group_admin_block"]
