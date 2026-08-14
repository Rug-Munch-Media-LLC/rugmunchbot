#!/usr/bin/env python3
"""
RugMunchBot -- DEPRECATED free-text message handler module (v5).

This module was the OLD free-text AI chat pipeline (``handle_message`` with
$TOKEN symbol lookup, bare-address auto-scan, tiered ``ai_chat_response``:
RAG -> LLM -> local intent -> redirect). It has been SUPERSEDED by
``handlers/chat_ai.py``, which is now the SINGLE free-text pipeline:

  * feature routing (owner admin console, bot kill-switch, known-scam block,
    bare-address auto-scan, $TOKEN price card)
  * the shared AI core ``chat_ai.chat_ai_response`` (backend RAG chat with
    news_v2 auto-grounding), used by free text, /chat and /ask

Nothing below is registered in bot.py anymore — ``register_chat_ai`` is the
only free-text MessageHandler. This file is kept ONLY for the persistent
owner admin reply-keyboard helpers, which ``handlers/scanning.py``
(``cmd_start``) still imports, and as a historical reference for the old
tiered pipeline. Do not re-add free-text handlers here.
"""

from __future__ import annotations

# Chat ids where the persistent admin reply keyboard is already attached.
_admin_kb_sent: set[int] = set()


def admin_kb_is_sent(chat_id: int) -> bool:
    return chat_id in _admin_kb_sent


def admin_kb_mark_sent(chat_id: int) -> None:
    _admin_kb_sent.add(chat_id)


def admin_kb_reset(chat_id: int) -> None:
    """Forget the sentinel so the next message re-attaches the keyboard."""
    _admin_kb_sent.discard(chat_id)
