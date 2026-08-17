#!/usr/bin/env python3
"""
RugMunchBot -- scan-family command handlers (degodded 2026-08-16).

The historic single-module ``_scan_handlers.py`` (1138 lines) was split into
focused submodules (mirroring the scanner/_llm/_routing splits):
``_progress``, ``_scan``, ``_deepscan``, ``_wallet`` and ``_extras``. This
package re-exports everything so existing importers/tests keep working
unchanged:

    from app.domains.telegram.rugmunchbot.handlers._scan_handlers import cmd_scan
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.handlers._scan_handlers._deepscan import (
    cmd_deepscan,
    sentinel_scan,
)
from app.domains.telegram.rugmunchbot.handlers._scan_handlers._extras import (
    cmd_deployer,
    cmd_hype,
    cmd_quick,
)
from app.domains.telegram.rugmunchbot.handlers._scan_handlers._progress import (
    _edit_stage_if_slow,
    _staged_progress,
    _stop_task,
)
from app.domains.telegram.rugmunchbot.handlers._scan_handlers._scan import (
    _poll_v2_scan_completion,
    cmd_scan,
)
from app.domains.telegram.rugmunchbot.handlers._scan_handlers._wallet import (
    cmd_compare,
    cmd_wallet,
)

__all__ = [
    "_edit_stage_if_slow",
    "_poll_v2_scan_completion",
    "_staged_progress",
    "_stop_task",
    "cmd_compare",
    "cmd_deepscan",
    "cmd_deployer",
    "cmd_hype",
    "cmd_quick",
    "cmd_scan",
    "cmd_wallet",
    "sentinel_scan",
]
