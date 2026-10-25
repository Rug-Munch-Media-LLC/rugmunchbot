"""Shared test seam: source of every channel-job registrar module.

Registration was split across sibling modules, so an assertion about "the
scheduler wires job X" must read all of them instead of the orchestrator only.
"""

from __future__ import annotations

import inspect

from rugmunchbot.channels.scheduler import (
    _register,
    _register_alerts,
    _register_alpha,
    _register_content,
    _register_daily,
    _register_intel,
    _register_kol,
    _register_lanes,
)

_MODULES = (
    _register,
    _register_alpha,
    _register_alerts,
    _register_kol,
    _register_content,
    _register_intel,
    _register_daily,
    _register_lanes,
)


def registrar_sources() -> str:
    """Concatenated source of every registrar — the ownership assertion seam."""
    return "\n".join(inspect.getsource(module) for module in _MODULES)


def flag_owner(flag: str):
    """The registrar module that owns ``flag`` (falls back to the orchestrator)."""
    for module in _MODULES:
        if hasattr(module, flag):
            return module
    return _register
