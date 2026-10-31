"""_local_v2.py - import surface preserved; implementation split into submodules."""

from __future__ import annotations

from typing import Any

from . import localv2_p1, localv2_p2, localv2_p3, localv2_p4
from .localv2_p3 import install_import_bridge
from .localv2_p4 import scan_token_v2_local

_PARTS: tuple[Any, ...] = (localv2_p1, localv2_p2, localv2_p3, localv2_p4)

__all__ = ["install_import_bridge", "scan_token_v2_local"]


def __getattr__(name: str) -> Any:
    """Resolve every other split symbol against the part that owns it.

    Re-importing each name here would freeze mutable module state at import
    time: ``_INSTALLED`` is flipped by ``install_import_bridge()``, so a copy
    bound in this module would stay ``False`` and break the documented
    "second call is a no-op" contract.
    """
    for part in _PARTS:
        if hasattr(part, name):
            return getattr(part, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
