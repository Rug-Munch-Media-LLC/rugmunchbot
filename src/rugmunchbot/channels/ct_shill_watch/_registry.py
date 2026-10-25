"""ct_shill_watch — curated CT registry + rotation cursor (fail-open)."""

from __future__ import annotations

import json

from rugmunchbot.support.logging import get_logger

from . import _config

logger = get_logger("rmi_bot.channels.ct_shill_watch")


def load_ct_handles(path: str | None = None) -> list[str]:
    """Handles from the curated influential-accounts registry (fail-open []).

    ``path`` overrides ``_config.INFLUENTIAL_ACCOUNTS_PATH`` (test seam).
    """
    try:
        with open(path or _config.INFLUENTIAL_ACCOUNTS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug("ct_shill:registry_load_failed err=%s", str(exc)[:120])
        return []
    handles: list[str] = []
    seen: set[str] = set()
    raw = data.get("handles") or data.get("accounts") or data.get("overlap_with_ct") or []
    for item in raw:
        h = (
            str(item if isinstance(item, str) else (item or {}).get("handle", ""))
            .strip()
            .lstrip("@")
            .lower()
        )
        if h and h not in seen:
            seen.add(h)
            handles.append(h)
    return handles


def _read_cursor(path: str, total: int) -> int:
    try:
        with open(path, encoding="utf-8") as fh:
            idx = int(json.load(fh).get("index", 0))
    except (OSError, ValueError, AttributeError):
        idx = 0
    return idx % total if total else 0


def _write_cursor(path: str, idx: int) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"index": idx, "updated": _config.now_iso()}, fh)
    except OSError as exc:
        logger.debug("ct_shill:cursor_write_failed err=%s", str(exc)[:120])


def next_account_batch(
    handles: list[str], count: int, cursor_path: str | None = None
) -> list[str]:
    """Next rotation slice of the CT registry (pure, cursor-advancing)."""
    if not handles or count <= 0:
        return []
    path = cursor_path or _config.CURSOR_PATH
    start = _read_cursor(path, len(handles))
    batch = [handles[(start + i) % len(handles)] for i in range(min(count, len(handles)))]
    _write_cursor(path, (start + len(batch)) % len(handles))
    return batch
