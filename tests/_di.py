"""Seam-injection helper — the fleet's single test patching path.

Replaces direct ``monkeypatch`` attribute patching (cc33b42 precedent,
ratchet 666->614): tests inject dependencies by setting a module attribute
through :func:`_setattr`, and the autouse ``_restore_di_seams`` fixture in
``tests/conftest.py`` restores every touched attribute after each test.

Semantics match ``pytest.MonkeyPatch.setattr``: dotted-path or live-object
targets, optional ``raising=False`` to tolerate missing attributes, LIFO
restore order.
"""

from __future__ import annotations

import importlib
from typing import Any

_UNSET = object()
_PATCHES: list[tuple[Any, str, Any, bool]] = []


def _setattr(target: Any, name: str, value: Any = _UNSET, *, raising: bool = True) -> None:
    """Set a seam attribute (dotted path or live object), restored after the test."""
    if value is _UNSET:
        mod_path, _, attr = str(target).rpartition(".")
        obj = importlib.import_module(mod_path)
        new = name
    else:
        obj, attr, new = target, name, value
    had = hasattr(obj, attr)
    if raising and not had:
        raise AttributeError(f"object {obj!r} has no attribute {attr!r}")
    _PATCHES.append((obj, attr, getattr(obj, attr, None) if had else None, had))
    setattr(obj, attr, new)


def restore_patches() -> None:
    """Restore every attribute ``_setattr`` touched (autouse fixture teardown)."""
    while _PATCHES:
        obj, attr, old, had = _PATCHES.pop()
        if had:
            setattr(obj, attr, old)
        else:
            delattr(obj, attr)
