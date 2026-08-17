"""Scan-report / SENTINEL formatting blocks for the RugMunch bot.

Re-export shim. During the Batch 14 de-godding pass the formatter bodies
moved to per-section submodules:

  * ``_scan_report_common``   — shared pure text helpers (``_clip``,
    ``_collapse_blank_lines``, ``_short_addr``).
  * ``_scan_report_risk``     — one-line verdict + consolidated Risk Signals
    section (``_verdict_text``, ``_RISK_SIGNAL_KEYS``,
    ``_format_risk_signals_section``).
  * ``_scan_report_sentinel`` — SENTINEL (AEGIS) flag renderers
    (``format_sentinel_flags``, ``_format_sentinel_pro``).
  * ``_scan_report_deepscan`` — SENTINEL deep-forensic report
    (``format_sentinel_deepscan_section``, ``format_sentinel_scan_report``).
  * ``_scan_report_wallet``   — Deployer + GMGN "Smart Money / Holders"
    sections (``_format_deployer_section``, ``_format_gmgn_section``).
  * ``_scan_report_token``    — token identity helpers (``_token_image_url``,
    ``_token_description``).
  * ``_scan_report_main``     — the main ``/scan`` report assembler
    (``format_scan_report``).

This module keeps the public API intact: every name that used to live here
is re-exported below, so ``formatters/_sentinel.py`` and all importers
(e.g. ``formatters/_wallet.py``'s ``from ..._scan_report import _clip``)
keep working unchanged.
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.formatters._scan_report_common import (
    _clip,
    _collapse_blank_lines,
    _short_addr,
)
from app.domains.telegram.rugmunchbot.formatters._scan_report_deepscan import (
    format_deep_ml_scan_report,
    format_sentinel_deepscan_section,
    format_sentinel_scan_report,
    render_analyst_read_block,
)
from app.domains.telegram.rugmunchbot.formatters._scan_report_main import (
    format_deep_scan_report,
    format_scan_report,
)
from app.domains.telegram.rugmunchbot.formatters._scan_report_risk import (
    _format_risk_signals_section,
    _verdict_text,
)
from app.domains.telegram.rugmunchbot.formatters._scan_report_sentinel import (
    _format_sentinel_pro,
    format_sentinel_flags,
)
from app.domains.telegram.rugmunchbot.formatters._scan_report_token import (
    _token_description,
    _token_image_url,
)
from app.domains.telegram.rugmunchbot.formatters._scan_report_wallet import (
    _format_deployer_section,
    _format_gmgn_section,
)
from app.domains.telegram.rugmunchbot.formatters._sentinel_helpers import (
    _SENTINEL_CODE_MAP,  # re-exported via _sentinel shim (listed in __all__)
    SENTINEL_MODULE_EMOJI,
    _bluechip_addresses,  # re-exported via _sentinel shim (listed in __all__)
    _bundle_alert_line,
    _decode_sentinel_warning,
    _deployer_history_line,
    _fmt_usd_amount,
    _module_warnings,
    _risk_level_emoji,
    _sentinel_free,
    _sentinel_recommendations,
    _wallet_freshness_line,
    is_bluechip,
)

# Explicit re-exports: names imported from the split submodules and
# re-exported through ``_sentinel`` are listed here so mypy's
# ``no_implicit_reexport`` (strict) stays satisfied.
__all__ = [
    "SENTINEL_MODULE_EMOJI",
    "_SENTINEL_CODE_MAP",
    "_bluechip_addresses",
    "_bundle_alert_line",
    "_clip",
    "_collapse_blank_lines",
    "_decode_sentinel_warning",
    "_deployer_history_line",
    "_fmt_usd_amount",
    "_format_deployer_section",
    "_format_gmgn_section",
    "_format_risk_signals_section",
    "_format_sentinel_pro",
    "_module_warnings",
    "_risk_level_emoji",
    "_sentinel_free",
    "_sentinel_recommendations",
    "_short_addr",
    "_token_description",
    "_token_image_url",
    "_verdict_text",
    "_wallet_freshness_line",
    "format_deep_ml_scan_report",
    "format_deep_scan_report",
    "format_scan_report",
    "format_sentinel_deepscan_section",
    "format_sentinel_flags",
    "format_sentinel_scan_report",
    "is_bluechip",
    "render_analyst_read_block",
]
