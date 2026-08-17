"""SENTINEL / scan-report / news formatting blocks for the RugMunch bot.

Re-export shim. During the Batch 13 de-godding pass the formatter bodies
moved to focused submodules:

  * ``_scan_report`` — SENTINEL flags, deep-forensic scan report, own-data
    intel helpers, the main ``/scan`` report and the professional sentinel
    renderer.
  * ``_wallet`` — wallet-report + wallet-composition formatting.
  * ``_news`` — news cards + news sentiment lines.

This module keeps the public API intact: every name is re-exported below,
so ``formatters/__init__.py`` and all importers keep working unchanged.
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.formatters._news import (
    format_news_card,
    format_news_sentiment,
)
from app.domains.telegram.rugmunchbot.formatters._scan_report import (
    _SENTINEL_CODE_MAP,
    SENTINEL_MODULE_EMOJI,
    _bluechip_addresses,
    _bundle_alert_line,
    _clip,
    _collapse_blank_lines,
    _decode_sentinel_warning,
    _deployer_history_line,
    _fmt_usd_amount,
    _format_deployer_section,
    _format_gmgn_section,
    _format_risk_signals_section,
    _format_sentinel_pro,
    _module_warnings,
    _risk_level_emoji,
    _sentinel_free,
    _sentinel_recommendations,
    _short_addr,
    _token_description,
    _token_image_url,
    _verdict_text,
    _wallet_freshness_line,
    format_deep_ml_scan_report,
    format_deep_scan_report,
    format_scan_report,
    format_sentinel_deepscan_section,
    format_sentinel_flags,
    format_sentinel_scan_report,
    is_bluechip,
    render_analyst_read_block,
)
from app.domains.telegram.rugmunchbot.formatters._wallet import (
    format_wallet_composition_section,
)

# Explicit re-exports: mypy's ``no_implicit_reexport`` (strict) requires names
# imported from the split submodules and re-exported through
# ``formatters/__init__`` to be listed here.
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
    "format_news_card",
    "format_news_sentiment",
    "format_scan_report",
    "format_sentinel_deepscan_section",
    "format_sentinel_flags",
    "format_sentinel_scan_report",
    "format_wallet_composition_section",
    "is_bluechip",
    "render_analyst_read_block",
]
