"""RugMunchBot — self-hostable Telegram crypto intelligence bot.

OSS package root. Public API is the portable product surface:

    rugmunchbot.formatters   — scan-report / wallet-report / news renderers
    rugmunchbot.keyboards    — inline-keyboard builders
    rugmunchbot.states       — spam protection, rate limits, SOL price cache
    rugmunchbot.tools        — tool registry + ``run_for_message`` intent router
    rugmunchbot.config       — env-only configuration

``bot.py`` (entry point), ``scanner.py`` (scan engines) and the command
handlers are ported in later stages — see
``docs/RUGMUNCHBOT-EXTRACTION-CHECKLIST.md`` for the migration plan.
"""

from __future__ import annotations

__version__ = "0.1.0"

# Re-export the portable public surface (mirrors the live package's
# backwards-compat re-exports for the modules that are already ported).
from rugmunchbot.formatters import (
    EVM_RE,
    SOL_RE,
    TOKEN_RE,
    detect_chain,
    footer_links,
    format_scan_report,
    format_wallet_report,
    is_evm,
    is_sol,
    paywall_text,
    sep,
    short_addr,
    social_proof_text,
    thin_sep,
    web_scan_button,
)
from rugmunchbot.keyboards import (
    back_kb,
    main_menu_kb,
    paywall_kb,
    pricing_kb,
    scan_result_kb,
    token_buttons,
)
from rugmunchbot.states import check_spam, get_sol_price, is_owner
from rugmunchbot.tools import (
    known_addresses,
    known_symbols,
    registry,
    run_for_message,
)

__all__ = [
    # formatters
    "EVM_RE",
    "SOL_RE",
    "TOKEN_RE",
    "__version__",
    # keyboards
    "back_kb",
    # states
    "check_spam",
    "detect_chain",
    "footer_links",
    "format_scan_report",
    "format_wallet_report",
    "get_sol_price",
    "is_evm",
    "is_owner",
    "is_sol",
    # tools
    "known_addresses",
    "known_symbols",
    "main_menu_kb",
    "paywall_kb",
    "paywall_text",
    "pricing_kb",
    "registry",
    "run_for_message",
    "scan_result_kb",
    "sep",
    "short_addr",
    "social_proof_text",
    "thin_sep",
    "token_buttons",
    "web_scan_button",
]
