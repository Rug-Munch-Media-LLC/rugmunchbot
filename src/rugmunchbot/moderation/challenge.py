"""Stateless short-math captcha — no storage, no reply parsing.

The seed is (chat, user, 5-minute bucket), so the buttons a member was shown
stay valid for the bucket even if the bot restarts, and a wrong tap is final.
"""

from __future__ import annotations

import time

BUCKET_S = 300
OPTIONS = 3


def challenge(
    chat_id: int, user_id: int, *, bucket_s: int = BUCKET_S, now: float | None = None
) -> tuple[str, tuple[int, ...], int]:
    """Return ``(question, options, answer)`` for this member right now."""
    stamp = int((time.time() if now is None else now) // bucket_s)
    seed = (abs(int(chat_id)) * 31 + int(user_id)) ^ stamp
    left = 2 + seed % 8
    right = 1 + (seed // 8) % 7
    answer = left + right
    choices = sorted({answer, max(1, answer - 2), answer + 3})
    while len(choices) < OPTIONS:  # pragma: no cover — arithmetic guarantees 3
        choices.append(max(choices) + 1)
    return f"{left} + {right} = ?", tuple(choices[:OPTIONS]), answer


def accepts(chat_id: int, user_id: int, answer: int, *, now: float | None = None) -> bool:
    """True when *answer* solves the current or the previous bucket's challenge."""
    stamp = time.time() if now is None else now
    valid = {
        challenge(chat_id, user_id, now=stamp)[2],
        challenge(chat_id, user_id, now=stamp - BUCKET_S)[2],
    }
    return int(answer) in valid


__all__ = ["BUCKET_S", "accepts", "challenge"]
