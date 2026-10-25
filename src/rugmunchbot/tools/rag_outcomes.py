"""Typed RAG lane outcomes — a failure is never an indistinguishable empty list.

Every RAG stage (embed, per-collection search, whole lane) returns one of these
so callers can tell ``no corpus hits`` from ``the embedder is down`` and can
carry a ``degraded`` marker into the post/answer that consumed the lane.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

#: Default corpus width — Qwen3-Embedding-8B is 4096-d (4B/2560 is RETIRED).
_DEFAULT_DIMS = 4096


def expected_dims() -> int:
    """Corpus vector width (env ``RAG_EMBED_DIMS``, 8B/4096 default)."""
    try:
        return int(os.getenv("RAG_EMBED_DIMS", str(_DEFAULT_DIMS)))
    except ValueError:
        return _DEFAULT_DIMS


@dataclass(frozen=True)
class EmbedOutcome:
    """Typed embed result — ``ok=False`` always carries a machine ``reason``."""

    vector: list[float] | None = None
    ok: bool = False
    reason: str = ""
    dims: int = 0


@dataclass(frozen=True)
class LaneOutcome:
    """Typed retrieval result — ``degraded`` means partial/incomplete coverage."""

    hits: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True
    degraded: bool = False
    reason: str = ""


__all__ = ["EmbedOutcome", "LaneOutcome", "expected_dims"]
