"""Channel guard / moderation for RugMunchBot group chats.

Default is DRY-RUN: every decision is decided and audited, nothing is sent,
deleted, muted or banned. A chat becomes live only when its own block in
``configs/moderation.json`` sets ``"dry_run": false``.

Layers: new-member captcha gate · link allowlist + scam-domain blocklist ·
promo/impersonation regex · repeat-hash + velocity windows · address scanner
hook · append-only JSONL audit with an admin pardon/appeal path.
"""

from __future__ import annotations

from rugmunchbot.moderation.config import ChatPolicy, load_chat_policy
from rugmunchbot.moderation.decide import decide as decide_message
from rugmunchbot.moderation.gate import moderation_gate
from rugmunchbot.moderation.models import Action, AddressVerdict, MsgContext
from rugmunchbot.moderation.register import register_moderation

__all__ = [
    "Action",
    "AddressVerdict",
    "ChatPolicy",
    "MsgContext",
    "decide_message",
    "load_chat_policy",
    "moderation_gate",
    "register_moderation",
]
