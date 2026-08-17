"""Scanner engine — short-TTL in-process scan cache (keyed by chain:address).

Repeat /scan of the same token is INSTANT: a 30s window lets the /scan
handler skip the whole provider chain. Fail-open: the cache is purely
in-memory and never raises — a miss just rescans live. Deliberately NOT
applied inside scan_token_v2 / scan_token (engine seams are mocked by
tests; a shared module-level cache would mask mocked provider outcomes).
"""

from __future__ import annotations

import copy
import time
from typing import Any

# ── Short-TTL scan cache (in-process, keyed by chain:address) ─────────────
# Repeat /scan of the same token is INSTANT: a 30s window lets the /scan
# handler skip the whole provider chain (backend v2 POST + DexScreener
# supplement + legacy fallback). Fail-open: the cache is purely in-memory and
# never raises — a miss just rescans live. 30s is short enough that risk data
# stays fresh while repeat scans of the same address (frequent in /scan +
# /deepscan) stay instant. NOTE: deliberately NOT applied inside
# scan_token_v2 / scan_token — engine seams are mocked by tests, and a
# shared module-level cache there would mask mocked provider outcomes.
_SCAN_CACHE_TTL = 30.0
_scan_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _scan_cache_key(address: str, chain: str) -> str:
    return f"{(chain or 'unknown').strip().lower()}:{(address or '').strip().lower()}"


def _scan_cache_get(address: str, chain: str) -> dict[str, Any] | None:
    """Return a deep copy of the cached scan for ``chain:address`` (or None).

    Deep copy: callers (e.g. ``_merge_completed_scan`` on /deepscan) mutate
    the returned payload; a copy guarantees the shared cache entry is never
    poisoned by a caller's in-place edit.
    """
    key = _scan_cache_key(address, chain)
    hit = _scan_cache.get(key)
    if hit is None:
        return None
    ts, data = hit
    if time.monotonic() - ts > _SCAN_CACHE_TTL:
        _scan_cache.pop(key, None)
        return None
    return copy.deepcopy(data)


def _scan_cache_set(address: str, chain: str, data: dict[str, Any]) -> None:
    """Cache a deep copy of ``data`` so callers mutating their own result can
    never poison the shared cache entry."""
    key = _scan_cache_key(address, chain)
    _scan_cache[key] = (time.monotonic(), copy.deepcopy(data))
