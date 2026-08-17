#!/usr/bin/env python3
"""RugMunchBot -- chat-history persistence for the free-text AI pipeline.

Split out of ``_routing.py`` during the de-godding pass. This module owns
the persistence step of ``chat_ai_response``: writing the user/assistant
messages to the chat history DB and bumping the usage / analytics counters
(``_persist_answer``).

``_routing.py`` re-exports ``_persist_answer``, so the public name and
signature are unchanged.
"""

from __future__ import annotations

import contextlib

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.admin_state import track_ai_query, track_tokens
from app.domains.telegram.rugmunchbot.db._base import run_db


async def _persist_answer(user_id: int, text: str, answer: str) -> None:
    """Persist the user/assistant messages + usage/analytics counters."""
    await run_db(db.increment_usage, user_id, ai_msg=True)
    await run_db(db.add_chat_message, user_id, "user", text)
    await run_db(db.add_chat_message, user_id, "assistant", answer)
    with contextlib.suppress(Exception):
        await track_ai_query()
        await track_tokens(len(answer) // 4)


__all__ = [
    "_persist_answer",
]
