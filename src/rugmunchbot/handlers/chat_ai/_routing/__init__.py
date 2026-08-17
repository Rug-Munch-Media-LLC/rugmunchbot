"""RugMunchBot -- chat routing for the free-text AI pipeline.

Split from the monolithic ``_routing.py`` (2026-08-16) into focused
submodules by responsibility. This ``__init__`` is a pure re-export shim:
every public name and signature is unchanged, and callers/tests keep
importing ``chat_ai._routing`` exactly as before. Patchable names
(``httpx``, ``TOKEN_CARD_DECIDE_WAIT``) stay bound here so tests that
monkeypatch ``chat_ai._routing.*`` keep working (submodules resolve them
through this package at call time).

Layout:
  * ``_constants.py`` — endpoint/budget/deadline constants
  * ``_context.py``   — best-effort scan + bridge context fetches
  * ``_token_card.py``— $TOKEN instant price card (race-safe)
  * ``_structural.py``— free-text feature routing (NO AI quota)
  * ``_core.py``      — the shared AI core (``chat_ai_response``)
  * ``_handler.py``  — entry gate + ``ai_chat_handler`` + wiring
"""

from __future__ import annotations

import httpx as httpx

from app.domains.telegram.rugmunchbot.handlers.chat_ai._delivery import (
    _edit_placeholder_if_slow as _edit_placeholder_if_slow,
    _keep_typing as _keep_typing,
    _send_answer as _send_answer,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm import (
    _build_fallback_answer as _build_fallback_answer,
    _gather_context as _gather_context,
    _route_tier as _route_tier,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._constants import (
    BRIDGE_CONTEXT_ENDPOINT as BRIDGE_CONTEXT_ENDPOINT,
    BRIDGE_TIMEOUT as BRIDGE_TIMEOUT,
    CHAT_TURN_DEADLINE as CHAT_TURN_DEADLINE,
    CHAT_TURN_DEADLINE_LONG as CHAT_TURN_DEADLINE_LONG,
    MAX_MESSAGE_CHARS as MAX_MESSAGE_CHARS,
    SCAN_ENDPOINT as SCAN_ENDPOINT,
    SCAN_TIMEOUT as SCAN_TIMEOUT,
    TIMEOUT_REPLY as TIMEOUT_REPLY,
    TOKEN_CARD_DECIDE_WAIT as TOKEN_CARD_DECIDE_WAIT,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._context import (
    _fetch_bridge_context as _fetch_bridge_context,
    _fetch_scan_context as _fetch_scan_context,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._core import chat_ai_response as chat_ai_response
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._handler import (
    _is_bot_addressed as _is_bot_addressed,
    ai_chat_handler as ai_chat_handler,
    register_chat_ai as register_chat_ai,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._structural import (
    _route_structural as _route_structural,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._token_card import (
    _cancel_token_lookup as _cancel_token_lookup,
    _lookup_token_symbol as _lookup_token_symbol,
    _safe_float as _safe_float,
    _token_card_won as _token_card_won,
    _TokenCardFlag as _TokenCardFlag,
)

__all__ = [
    "SCAN_ENDPOINT",
    "SCAN_TIMEOUT",
    "TOKEN_CARD_DECIDE_WAIT",
    "_is_bot_addressed",
    "_lookup_token_symbol",
    "_route_structural",
    "_send_answer",
    "_token_card_won",
    "ai_chat_handler",
    "chat_ai_response",
    "register_chat_ai",
]
