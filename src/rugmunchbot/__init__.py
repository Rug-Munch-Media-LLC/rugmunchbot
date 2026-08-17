"""
RugMunchBot subsystem - modular architecture.

Re-exports key symbols for backward compatibility.
"""

# Re-export from new modules for backward compatibility
# Re-export main entry point
from app.domains.telegram.rugmunchbot.bot import main
from app.domains.telegram.rugmunchbot.formatters import (
    EVM_RE,
    SOL_RE,
    TOKEN_RE,
    detect_chain,
    footer_links,
    format_scan_report,
    format_wallet_report,
    is_evm,
    is_owner,
    is_sol,
    paywall_text,
    sep,
    short_addr,
    social_proof_text,
    thin_sep,
    web_scan_button,
)
from app.domains.telegram.rugmunchbot.keyboards import (
    back_kb,
    buy_stars_kb,
    main_menu_kb,
    paywall_kb,
    pricing_kb,
    scamschool_kb,
    scan_result_kb,
    social_kb,
    topup_kb,
)
from app.domains.telegram.rugmunchbot.scanner import analyze_wallet, scan_token
from app.domains.telegram.rugmunchbot.states import check_spam, get_sol_price, is_owner

__all__ = [
    "EVM_RE",
    "SOL_RE",
    "TOKEN_RE",
    "analyze_wallet",
    "back_kb",
    "buy_stars_kb",
    "check_spam",
    "detect_chain",
    "footer_links",
    "format_scan_report",
    "format_wallet_report",
    "get_sol_price",
    "is_evm",
    "is_owner",
    "is_sol",
    "main",
    "main_menu_kb",
    "paywall_kb",
    "paywall_text",
    "pricing_kb",
    "scamschool_kb",
    "scan_result_kb",
    "scan_token",
    "sep",
    "short_addr",
    "social_kb",
    "social_proof_text",
    "thin_sep",
    "topup_kb",
    "web_scan_button",
]
