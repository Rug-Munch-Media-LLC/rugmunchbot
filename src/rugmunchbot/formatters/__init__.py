#!/usr/bin/env python3
"""
RugMunchBot -- Message formatting & utility helpers.

Split into focused submodules (Batch 11 de-godding):
  * ``formatters._base``     -- address detection, text/link helpers, welcome
                                & paywall text, explainable scoring blocks.
  * ``formatters._sentinel`` -- SENTINEL flags, deep-forensic scan report,
                                own-data intel helpers, /scan report, news.
  * ``formatters._wallet``   -- wallet-report formatting.

This module keeps the public API intact: every name that used to live here is
re-exported below, so ``from ...formatters import X`` keeps working unchanged.
``is_owner`` intentionally stays defined here (it reads the module-global
``OWNER_IDS``, which tests monkeypatch on this module).
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.config import OWNER_IDS, fmt_chain, fmt_number, fmt_pct
from app.domains.telegram.rugmunchbot.formatters._base import (
    BETA_BANNER,
    BETA_PAYMENTS_LINE,
    BETA_STATUS_LINE,
    DIMENSION_LABELS,
    EVM_RE,
    SOL_RE,
    TOKEN_RE,
    _price_diff_pct,
    _risk_indicator,
    about_text,
    detect_chain,
    dex_buttons,
    dexscreener_url,
    explorer_url,
    faq_text,
    footer_links,
    format_funding_analysis,
    format_scoring_breakdown,
    format_verdict_header,
    gmgn_url,
    is_evm,
    is_sol,
    paywall_text,
    sep,
    short_addr,
    social_proof_text,
    thin_sep,
    web_scan_button,
    welcome_card_text,
)
from app.domains.telegram.rugmunchbot.formatters._sentinel import (
    _SENTINEL_CODE_MAP,
    SENTINEL_MODULE_EMOJI,
    _bluechip_addresses,
    _bundle_alert_line,
    _decode_sentinel_warning,
    _deployer_history_line,
    _fmt_usd_amount,
    _format_deployer_section,
    _format_sentinel_pro,
    _module_warnings,
    _risk_level_emoji,
    _sentinel_free,
    _sentinel_recommendations,
    _wallet_freshness_line,
    format_deep_ml_scan_report,
    format_deep_scan_report,
    format_news_card,
    format_news_sentiment,
    format_scan_report,
    format_sentinel_deepscan_section,
    format_sentinel_flags,
    format_sentinel_scan_report,
    format_wallet_composition_section,
    is_bluechip,
    render_analyst_read_block,
)
from app.domains.telegram.rugmunchbot.formatters._wallet import (
    _activity_score,
    _explorer_link,
    format_wallet_report,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")


def is_owner(uid: int) -> bool:
    """True when the user id is in the owner allowlist."""
    return uid in OWNER_IDS


__all__ = [
    "BETA_BANNER",
    "BETA_PAYMENTS_LINE",
    "BETA_STATUS_LINE",
    "DIMENSION_LABELS",
    "EVM_RE",
    "SENTINEL_MODULE_EMOJI",
    "SOL_RE",
    "TOKEN_RE",
    "_SENTINEL_CODE_MAP",
    "_activity_score",
    "_bluechip_addresses",
    "_bundle_alert_line",
    "_decode_sentinel_warning",
    "_deployer_history_line",
    "_explorer_link",
    "_fmt_usd_amount",
    "_format_deployer_section",
    "_format_sentinel_pro",
    "_module_warnings",
    "_price_diff_pct",
    "_risk_indicator",
    "_risk_level_emoji",
    "_sentinel_free",
    "_sentinel_recommendations",
    "_wallet_freshness_line",
    "about_text",
    "detect_chain",
    "dex_buttons",
    "dexscreener_url",
    "explorer_url",
    "faq_text",
    "fmt_chain",
    "fmt_number",
    "fmt_pct",
    "footer_links",
    "format_deep_ml_scan_report",
    "format_deep_scan_report",
    "format_funding_analysis",
    "format_news_card",
    "format_news_sentiment",
    "format_scan_report",
    "format_scoring_breakdown",
    "format_sentinel_deepscan_section",
    "format_sentinel_flags",
    "format_sentinel_scan_report",
    "format_verdict_header",
    "format_wallet_composition_section",
    "format_wallet_report",
    "gmgn_url",
    "is_bluechip",
    "is_evm",
    "is_owner",
    "is_sol",
    "paywall_text",
    "render_analyst_read_block",
    "sep",
    "short_addr",
    "social_proof_text",
    "thin_sep",
    "web_scan_button",
    "welcome_card_text",
]
