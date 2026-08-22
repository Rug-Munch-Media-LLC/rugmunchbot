"""trend_algo pure normalisation/coercion helpers.

Extracted verbatim from the former single-module ``tools/trend_algo.py``.
"""

from __future__ import annotations

import math
from typing import Any

from rugmunchbot.tools.trend_algo._constants import _CHAIN_ALIASES


def _normalize_chain(chain: str) -> str:
    """Return canonical DexScreener chain id, or the raw value if unknown."""
    key = chain.strip().lower()
    if key in ("", "all", "any", "multi"):
        return "all"
    return _CHAIN_ALIASES.get(key, key)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _exp_norm(value: float, reference: float) -> float:
    if reference <= 0:
        return 0.0
    return 1.0 - math.exp(-max(value, 0.0) / reference)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
