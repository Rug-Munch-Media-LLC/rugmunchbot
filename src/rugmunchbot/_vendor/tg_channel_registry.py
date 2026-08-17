# VENDORED 2026-08-17 from rmi-backend/app/databus/tg_channel_registry.py (Phase 2a — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Telegram-channel source registry for news monitoring + scammer-caller tracking.

Pure JSON registry (``app/databus/tg_channels.json``) with a thin validation
layer, mirroring the style of the other databus registries
(``unified_news_sources.json``, ``known_scammers.json``). No network access —
the registry only records channels; scraping/telegram access lives elsewhere.

Entry schema
------------
``username``            Telegram handle (may include leading ``@``)
``name``                Display name
``kind``                ``"news"`` | ``"scammer_caller"`` | ``"scammer_group"`` | ``"intel"``
``category``            News category (e.g. ``crypto_news``) or scam type
                        (e.g. ``phishing_alerts``, ``shill_promotions``)
``reliable``            bool — trustworthy source (scam/shill channels are False)
``scrape_times_per_day`` int >= 0 — desired scrape frequency
``notes``               Free-form (verification confidence, flags)
``enabled``             bool — participate in active monitoring

Validation
----------
Entries missing a required field, with an unknown ``kind``, or with a wrong
type for a boolean/int field are **skipped** at load (``load_tg_channels``).
Optional fields default: ``reliable=False``, ``enabled=True``,
``scrape_times_per_day=0``, ``notes=""``.

The starter registry is intentionally small. The owner drops in their full
list by editing ``tg_channels.json`` or by calling :func:`upsert_channel` per
entry; deduplication is by normalized username (``@`` stripped, lowercased).
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

TG_CHANNELS_FILE = os.path.join(os.path.dirname(__file__), "tg_channels.json")

VALID_KINDS = frozenset({"news", "scammer_caller", "scammer_group", "intel"})

_REQUIRED_FIELDS = ("username", "name", "kind", "category")
_BOOL_FIELDS = ("reliable", "enabled")


def channel_dedup_key(entry: dict[str, Any] | str) -> str:
    """Normalized deduplication key for a channel: ``@`` stripped, lowercased.

    Accepts either a full entry dict or a raw username string.
    """
    username = entry if isinstance(entry, str) else str(entry.get("username") or "")
    return username.strip().lstrip("@").strip().lower()


def _is_valid(entry: Any) -> bool:
    """True when ``entry`` is a well-formed tg_channel record."""
    if not isinstance(entry, dict):
        return False
    for field in _REQUIRED_FIELDS:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            return False
    if entry["kind"] not in VALID_KINDS:
        return False
    for field in _BOOL_FIELDS:
        if field in entry and not isinstance(entry[field], bool):
            return False
    if "scrape_times_per_day" in entry:
        value = entry["scrape_times_per_day"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
    return True


def _normalize(entry: dict[str, Any]) -> dict[str, Any]:
    """Fill documented defaults for optional fields (validated copy)."""
    normalized = dict(entry)
    normalized.setdefault("reliable", False)
    normalized.setdefault("enabled", True)
    normalized.setdefault("scrape_times_per_day", 0)
    normalized.setdefault("notes", "")
    return normalized


def load_tg_channels(path: str | None = None) -> list[dict[str, Any]]:
    """Load the registry, skip malformed entries, sort by kind then username.

    Tolerates a missing file (returns ``[]``) and either a bare list or a dict
    with a ``channels`` key at the top level.
    """
    if path is None:
        path = TG_CHANNELS_FILE
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("channels", [])
    if not isinstance(data, list):
        return []
    channels = [_normalize(e) for e in data if _is_valid(e)]
    channels.sort(key=lambda e: (e["kind"], channel_dedup_key(e)))
    return channels


def channels_by_kind(kind: str, path: str | None = None) -> list[dict[str, Any]]:
    """All registry entries with the given ``kind``."""
    return [e for e in load_tg_channels(path) if e.get("kind") == kind]


def channels_by_category(category: str, path: str | None = None) -> list[dict[str, Any]]:
    """All registry entries with the given ``category``."""
    return [e for e in load_tg_channels(path) if e.get("category") == category]


def enabled_channels(path: str | None = None) -> list[dict[str, Any]]:
    """All registry entries that are enabled for active monitoring."""
    return [e for e in load_tg_channels(path) if e.get("enabled") is True]


def upsert_channel(entry: dict[str, Any], path: str | None = None) -> dict[str, Any]:
    """Insert or replace a channel (dedupe by normalized username), persisting to disk.

    Returns the stored (normalized) entry. Raises ``ValueError`` on a malformed
    entry. Existing top-level registry metadata (version/note/categories) is
    preserved; ``updated`` and ``total_channels`` are refreshed.
    """
    if path is None:
        path = TG_CHANNELS_FILE
    if not _is_valid(entry):
        raise ValueError(f"invalid tg_channel entry: {entry!r}")
    normalized = _normalize(entry)
    channels = load_tg_channels(path)
    key = channel_dedup_key(normalized)
    replaced = False
    for i, existing in enumerate(channels):
        if channel_dedup_key(existing) == key:
            channels[i] = normalized
            replaced = True
            break
    if not replaced:
        channels.append(normalized)
    channels.sort(key=lambda e: (e["kind"], channel_dedup_key(e)))

    top: dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                existing_top = json.load(f)
        except (OSError, json.JSONDecodeError):
            existing_top = {}
        if isinstance(existing_top, dict):
            top = existing_top
    top.setdefault("version", "1.0")
    top["updated"] = date.today().isoformat()
    top["total_channels"] = len(channels)
    top["channels"] = channels
    with open(path, "w") as f:
        json.dump(top, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return normalized


def to_news_source(entry: dict[str, Any]) -> dict[str, Any]:
    """Map a tg_channel entry into the ``unified_news_sources.json`` source shape.

    Produces the shape only — the unified registry file itself is NOT modified.
    ``category`` is passed through unchanged (the owner may map scam categories
    to ``"security"`` when wiring these into the news pipeline). Raises
    ``ValueError`` on a malformed entry.
    """
    if not _is_valid(entry):
        raise ValueError(f"invalid tg_channel entry: {entry!r}")
    username = channel_dedup_key(entry)
    reliable = bool(entry.get("reliable", False))
    return {
        "id": username,
        "name": str(entry.get("name", username)),
        "url": f"https://t.me/{username}",
        "category": str(entry["category"]),
        "type": "telegram",
        "priority": 1 if reliable else 2,
        "quality_tier": "established" if reliable else "independent",
        "language": "en",
        "requires_api_key": False,
        "verified": reliable,
        "needs_verification": not reliable,
    }
