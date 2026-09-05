#!/usr/bin/env python3
"""Shared helpers for the ``db.kol`` package."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def now_iso() -> str:
    """Current UTC timestamp as ISO-8601 (the kol tables' clock)."""
    return datetime.now(UTC).isoformat()


def norm_handle(handle: str) -> str:
    """Canonical handle form: trimmed, ``@``-stripped, lowercased."""
    return str(handle or "").strip().lstrip("@").lower()


def json_list(value: Any) -> list[str]:
    """Coerce a JSON-string / list column value into ``list[str]``."""
    import json

    if isinstance(value, list):
        return [str(v) for v in value]
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def wallet_entries(value: Any) -> list[dict[str, str]]:
    """Coerce the wallets column (JSON list of dicts, legacy bare strings)."""
    import json

    if isinstance(value, list):
        raw = value
    elif not value:
        return []
    else:
        try:
            raw = json.loads(str(value))
        except (ValueError, TypeError):
            return []
        if not isinstance(raw, list):
            return []
    out: list[dict[str, str]] = []
    for v in raw:
        if isinstance(v, dict):
            out.append(
                {
                    "address": str(v.get("address") or ""),
                    "chain": str(v.get("chain") or ""),
                    "source": str(v.get("source") or ""),
                    "evidence_url": str(v.get("evidence_url") or ""),
                    "confidence": str(v.get("confidence") or ""),
                }
            )
        else:
            out.append(
                {
                    "address": str(v),
                    "chain": "",
                    "source": "legacy",
                    "evidence_url": "",
                    "confidence": "",
                }
            )
    return out
