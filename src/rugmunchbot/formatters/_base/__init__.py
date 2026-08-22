"""Shared formatter foundation (package).

Modularized from the former single-module ``formatters/_base.py``
(2026-08-21). This ``__init__`` is a pure re-export shim: every public name
and signature is unchanged, and callers keep importing
``formatters._base`` exactly as before.

Layout:
  * ``_primitives.py`` — chain detection, address shortening, separators
  * ``_links.py``      — footer links, referral codes, DEX/explorer URLs
  * ``_buttons.py``    — per-chain trade-button rows with ref attribution
  * ``_texts.py``      — welcome / about / FAQ / paywall card texts
  * ``_risk.py``       — verdict headers, scoring breakdowns, funding analysis
"""

from __future__ import annotations

from rugmunchbot.formatters._base._buttons import (
    DEX_BUTTON_PRIORITY as DEX_BUTTON_PRIORITY,
    DEX_BUTTONS_PER_ROW as DEX_BUTTONS_PER_ROW,
    UNKNOWN_CHAIN_MINIMAL as UNKNOWN_CHAIN_MINIMAL,
    _ref_platform_buttons as _ref_platform_buttons,
    dex_button_rows as dex_button_rows,
    dex_buttons as dex_buttons,
)
from rugmunchbot.formatters._base._links import (
    DEXSCREENER_CHAINS as DEXSCREENER_CHAINS,
    DEXTOOLS_CHAINS as DEXTOOLS_CHAINS,
    GMGN_CHAINS as GMGN_CHAINS,
    REF_CODES as REF_CODES,
    _ref_code as _ref_code,
    dexscreener_url as dexscreener_url,
    dextools_url as dextools_url,
    explorer_url as explorer_url,
    footer_links as footer_links,
    gmgn_url as gmgn_url,
    web_scan_button as web_scan_button,
)
from rugmunchbot.formatters._base._primitives import (
    BETA_BANNER as BETA_BANNER,
    BETA_PAYMENTS_LINE as BETA_PAYMENTS_LINE,
    BETA_STATUS_LINE as BETA_STATUS_LINE,
    EVM_RE as EVM_RE,
    SOL_RE as SOL_RE,
    TOKEN_RE as TOKEN_RE,
    detect_chain as detect_chain,
    is_evm as is_evm,
    is_sol as is_sol,
    sep as sep,
    short_addr as short_addr,
    thin_sep as thin_sep,
)
from rugmunchbot.formatters._base._risk import (
    DIMENSION_LABELS as DIMENSION_LABELS,
    RISK_BAR_EMPTY as RISK_BAR_EMPTY,
    RISK_BAR_FILLED as RISK_BAR_FILLED,
    RISK_EMOJI as RISK_EMOJI,
    RISK_HIGH_SCORE as RISK_HIGH_SCORE,
    RISK_MID_SCORE as RISK_MID_SCORE,
    RISK_UNKNOWN_EMOJI as RISK_UNKNOWN_EMOJI,
    RISK_VERDICT as RISK_VERDICT,
    _pct_emoji as _pct_emoji,
    _price_diff_pct as _price_diff_pct,
    _risk_indicator as _risk_indicator,
    _risk_level as _risk_level,
    _verdict_sentence as _verdict_sentence,
    format_funding_analysis as format_funding_analysis,
    format_scoring_breakdown as format_scoring_breakdown,
    format_verdict_header as format_verdict_header,
)
from rugmunchbot.formatters._base._texts import (
    about_text as about_text,
    faq_text as faq_text,
    paywall_text as paywall_text,
    social_proof_text as social_proof_text,
    welcome_card_text as welcome_card_text,
)

__all__ = [
    "BETA_BANNER",
    "BETA_PAYMENTS_LINE",
    "BETA_STATUS_LINE",
    "DEXSCREENER_CHAINS",
    "DEXTOOLS_CHAINS",
    "GMGN_CHAINS",
    "REF_CODES",
    "RISK_EMOJI",
    "RISK_VERDICT",
    "about_text",
    "detect_chain",
    "dex_button_rows",
    "dex_buttons",
    "dexscreener_url",
    "dextools_url",
    "explorer_url",
    "faq_text",
    "footer_links",
    "format_funding_analysis",
    "format_scoring_breakdown",
    "format_verdict_header",
    "gmgn_url",
    "is_evm",
    "is_sol",
    "paywall_text",
    "sep",
    "short_addr",
    "social_proof_text",
    "thin_sep",
    "web_scan_button",
    "welcome_card_text",
]
