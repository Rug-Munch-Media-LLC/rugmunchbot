"""Specified voice for rugmunchbot: registers, deterministic phrasing, bans.

Three surfaces only:

* :func:`register_for` — pick the register (witty / neutral / serious) for a
  situation before writing copy.
* :func:`render` — fetch the copy for a message key, deterministically seeded
  by the message id, so the same input yields the same words.
* :func:`banned_hits` — the lint's oracle. Any hit is a defect.

Doctrine: ``docs/VOICE.md``.
"""

from __future__ import annotations

from rugmunchbot.voice._banned import BANNED_EMOJI, BOT_BANNED_PHRASES, banned_hits
from rugmunchbot.voice._registers import NEVER_SAY, SITUATION_REGISTER, Register
from rugmunchbot.voice._render import render
from rugmunchbot.voice._select import register_for
from rugmunchbot.voice._variants import VARIANTS

__all__ = [
    "BANNED_EMOJI",
    "BOT_BANNED_PHRASES",
    "NEVER_SAY",
    "SITUATION_REGISTER",
    "VARIANTS",
    "Register",
    "banned_hits",
    "register_for",
    "render",
]
