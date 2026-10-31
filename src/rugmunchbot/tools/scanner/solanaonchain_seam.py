"""Late-bound collaborator seams for the split ``_solana_onchain`` package.

Before the split every collaborator lived in one module namespace, and
``tests/_di.py`` injects fakes by patching exactly those attributes on the
public module (``_router``, the resolvers). A split part that binds a
collaborator with ``from ... import name`` freezes the import-time object and
silently ignores the injected fake.

The parts therefore resolve such collaborators through the public module at
call time; :func:`seam` returns the live attribute when it is present and the
part's own definition otherwise.
"""

from __future__ import annotations

import sys
from typing import Any

_MISSING = object()
_PUBLIC_MODULE = f"{__package__}._solana_onchain"


def seam(name: str, fallback: Any) -> Any:
    """Return the collaborator as the public module currently exposes it."""
    module = sys.modules.get(_PUBLIC_MODULE)
    if module is not None:
        current = getattr(module, name, _MISSING)
        if current is not _MISSING:
            return current
    return fallback
