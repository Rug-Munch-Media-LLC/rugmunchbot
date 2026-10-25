"""Deterministic variant selection.

Same key + same message id + same context => byte-identical output, across
processes and restarts. The pick uses BLAKE2s, not ``hash()``, because
``hash()`` is salted per process and would make the bot's voice drift on every
deploy — the opposite of a specified voice.

Determinism is the feature: a user who re-runs a command gets the same words,
so the personality reads as authored rather than randomised.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from rugmunchbot.voice._variants import VARIANTS

if TYPE_CHECKING:
    from collections.abc import Mapping


def _pick(message_id: int | str, key: str, count: int) -> int:
    """Stable index in ``[0, count)`` derived from the message identity."""
    if count <= 1:
        return 0
    seed = f"{message_id}\x1f{key}".encode()
    return hashlib.blake2s(seed, digest_size=8).digest()[0] % count


def render(
    key: str, context: Mapping[str, object] | None = None, *, message_id: int | str = 0
) -> str:
    """Render the message ``key`` with ``context``, deterministically.

    Raises ``KeyError`` for an unknown key — a missing phrase is a wiring bug,
    and falling back to a default string is how bot-sounding filler gets
    shipped.
    """
    if key not in VARIANTS:
        raise KeyError(f"voice: no such message key {key!r}")
    _register, variants = VARIANTS[key]
    template = variants[_pick(message_id, key, len(variants))]
    return template.format(**(context or {}))
