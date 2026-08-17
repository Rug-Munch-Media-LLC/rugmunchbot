"""RugMunchBot -- OSINT command handlers (/osint /track /investigate /email /phone /face /osintall).

Wraps the self-hosted OSINT section (``app.osint`` + ``app.osint_tools``) into
Telegram commands. Every lookup renders an :class:`OsintReport` (or, for
usernames, an :class:`OsintResult`) as a compact HTML message: top findings
first, then "+N more…". Graceful degradation: an unavailable tool/service or
any exception becomes a helpful message instead of a crash.

Split into focused submodules (de-godded):
  * ``_common``          -- shared tunables (timeouts, emoji maps, category
                            regexes) + input validators
  * ``_format``          -- HTML message rendering helpers
  * ``username_cmd``     -- /osint  (maigret username enumeration)
  * ``wallet_cmd``       -- /track  (wallet attribution)
  * ``domain_cmd``       -- /investigate (domain OSINT + Pro deep report)
  * ``email_cmd``        -- /email  (HIBP / emailrep.io)
  * ``phone_cmd``        -- /phone  (carrier / region / line-type risk)
  * ``face_cmd``         -- /face   (photo face detection + reverse search)
  * ``osintall_cmd``     -- /osintall (aggregate every module)

This module keeps the public API intact: every command handler is re-exported
below, so ``from ...handlers.osint import cmd_*`` keeps working unchanged.
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.handlers.osint.domain_cmd import cmd_investigate
from app.domains.telegram.rugmunchbot.handlers.osint.email_cmd import cmd_email
from app.domains.telegram.rugmunchbot.handlers.osint.face_cmd import cmd_face
from app.domains.telegram.rugmunchbot.handlers.osint.osintall_cmd import cmd_osintall
from app.domains.telegram.rugmunchbot.handlers.osint.phone_cmd import cmd_phone
from app.domains.telegram.rugmunchbot.handlers.osint.username_cmd import cmd_osint
from app.domains.telegram.rugmunchbot.handlers.osint.wallet_cmd import cmd_track

__all__ = [
    "cmd_email",
    "cmd_face",
    "cmd_investigate",
    "cmd_osint",
    "cmd_osintall",
    "cmd_phone",
    "cmd_track",
]
