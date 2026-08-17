#!/usr/bin/env python3
"""
RugMunchBot -- Core scanning command handlers (re-export shim).

Commands: start, help, scan, wallet, ta, compare, quick, deployer, hype,
sentinelscan.

NOTE (degodding 2026-08-14): the handler bodies now live in the
``handlers/_*`` submodules:

  * ``handlers/_core_handlers.py``  — cmd_start, cmd_help, cmd_faq
  * ``handlers/_scan_handlers.py``  — cmd_scan, cmd_deepscan, sentinel_scan,
    cmd_wallet, cmd_compare, cmd_quick, cmd_deployer, cmd_hype +
    ``_poll_v2_scan_completion``
  * ``handlers/_ta_handlers.py``    — cmd_ta
  * ``handlers/_charts.py``         — _fetch_rugcharts_ohlcv, _fetch_rugcharts_intel
  * ``handlers/_image.py``          — _maybe_send_token_image

This module is a thin re-export shim so existing importers/tests keep
working unchanged: ``bot.py`` imports the ``cmd_*`` handlers, ``chat_ai``
LAZILY imports ``cmd_start``/``cmd_scan`` (circular-import avoidance), and
tests import ``HELP_TEXT``/``format_ta_card``/``_maybe_send_token_image``
etc. from here. The ``_help_text`` and ``_scanning_helpers`` re-exports are
preserved with their ``# noqa: F401`` markers.
"""

from __future__ import annotations

__all__ = [
    "CHANNEL_LABELS",
    "HELP_TEXT",
    "MENU_TEXT",
    # re-exported from _scanning_helpers (tests)
    "_DEPLOYER_BUCKET_EMOJI",
    # re-exported from _help_text (callers/tests)
    "_HELP_EMOJI",
    "_HELP_SECTIONS",
    "_MENU_EMOJI",
    "_MENU_SECTIONS",
    "_bucket_emoji",
    "_build_help_text",
    "_build_menu_text",
    "_channel_url",
    "_channels_section",
    "_ds_created_iso",
    "_ds_pairs",
    "_fetch_rugcharts_intel",
    "_fetch_rugcharts_ohlcv",
    "_maybe_send_token_image",
    "_merge_completed_scan",
    "_poll_v2_scan_completion",
    "_ta_arrow",
    "_ta_rsi_emoji",
    "_ta_vol_arrow",
    # internal helpers
    "cmd_compare",
    "cmd_deepscan",
    "cmd_deployer",
    "cmd_faq",
    "cmd_help",
    "cmd_hype",
    "cmd_menu",
    "cmd_quick",
    # scan-family handlers
    "cmd_scan",
    # core handlers
    "cmd_start",
    # TA / charts / image
    "cmd_ta",
    "cmd_wallet",
    "format_ta_card",
    "sentinel_scan",
]

from app.domains.telegram.rugmunchbot.handlers._charts import (  # re-exported
    _fetch_rugcharts_intel,
    _fetch_rugcharts_ohlcv,
)
from app.domains.telegram.rugmunchbot.handlers._core_handlers import (  # re-exported
    cmd_faq,
    cmd_help,
    cmd_menu,
    cmd_start,
)
from app.domains.telegram.rugmunchbot.handlers._help_text import (  # re-exported for callers/tests
    _HELP_EMOJI,
    _HELP_SECTIONS,
    _MENU_EMOJI,
    _MENU_SECTIONS,
    CHANNEL_LABELS,
    HELP_TEXT,
    MENU_TEXT,
    _build_help_text,
    _build_menu_text,
    _channel_url,
    _channels_section,
)
from app.domains.telegram.rugmunchbot.handlers._image import _maybe_send_token_image  # re-exported
from app.domains.telegram.rugmunchbot.handlers._scan_handlers import (  # re-exported
    _poll_v2_scan_completion,
    cmd_compare,
    cmd_deepscan,
    cmd_deployer,
    cmd_hype,
    cmd_quick,
    cmd_scan,
    cmd_wallet,
    sentinel_scan,
)
from app.domains.telegram.rugmunchbot.handlers._scanning_helpers import (  # re-exported for tests
    _DEPLOYER_BUCKET_EMOJI,
    _bucket_emoji,
    _ds_created_iso,
    _ds_pairs,
    _merge_completed_scan,
    _ta_arrow,
    _ta_rsi_emoji,
    _ta_vol_arrow,
    format_ta_card,
)
from app.domains.telegram.rugmunchbot.handlers._ta_handlers import cmd_ta  # re-exported
