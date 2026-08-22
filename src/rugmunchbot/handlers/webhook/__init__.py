#!/usr/bin/env python3
"""
RugMunchBot -- /webhook command: Custom webhook management (package).

Modularized from the former single-module ``handlers/webhook.py`` (2026-08-22).
This ``__init__`` is a pure re-export shim: every public name and signature is
unchanged, and callers keep importing ``handlers.webhook`` exactly as before.

Pro tier. Users can set up webhooks to receive real-time data
about token events, price movements, wallet activity, etc.

Layout:
  * ``_constants.py`` — event catalog + backend event mapping
  * ``_security.py``  — URL validation + SSRF guards (DNS-rebinding safe)
  * ``_commands.py``  — /webhook add/list/remove/test/events handlers
"""

from __future__ import annotations

from rugmunchbot.handlers.webhook._commands import (
    _add_webhook as _add_webhook,
    _list_events as _list_events,
    _list_webhooks as _list_webhooks,
    _remove_webhook as _remove_webhook,
    _test_webhook as _test_webhook,
    cmd_webhook as cmd_webhook,
)
from rugmunchbot.handlers.webhook._constants import (
    BACKEND_EVENT_MAP as BACKEND_EVENT_MAP,
    WEBHOOK_EVENT_EMOJIS as WEBHOOK_EVENT_EMOJIS,
    WEBHOOK_EVENTS as WEBHOOK_EVENTS,
)
from rugmunchbot.handlers.webhook._security import (
    _assert_safe_target as _assert_safe_target,
    _is_blocked_address as _is_blocked_address,
    _validate_webhook_url as _validate_webhook_url,
)

__all__ = [
    "BACKEND_EVENT_MAP",
    "WEBHOOK_EVENTS",
    "WEBHOOK_EVENT_EMOJIS",
    "cmd_webhook",
]
