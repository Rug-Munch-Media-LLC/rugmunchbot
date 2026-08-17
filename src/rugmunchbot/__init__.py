"""
RugMunchBot subsystem - modular architecture.

Re-exports key symbols for backward compatibility. Phase 3: the heavy
re-exports are LAZY so light consumers (backend admin routers importing
``rugmunchbot.db`` / ``admin_state`` / ``config``) do not drag in the full
bot (telegram stack, osint, phonenumbers) at package import time.
"""

__all__: list[str] = []

# Lazy re-exports for backward compatibility (avoids heavy import chain).
# Direct imports of the submodules (``from rugmunchbot.bot import main``)
# work regardless; this keeps ``import rugmunchbot`` cheap for the backend.
from rugmunchbot import db  # noqa: E402  (lightweight, no bot deps)
from rugmunchbot import config  # noqa: E402  (lightweight, env-driven)


def __getattr__(name: str):
    """Lazily resolve heavy re-exports on attribute access."""
    if name == "main":
        from rugmunchbot.bot import main

        return main
    if name in ("EVM_RE", "SOL_RE", "TOKEN_RE", "detect_chain", "footer_links",
                "format_scan_report", "format_wallet_report", "is_evm", "is_owner",
                "is_sol", "paywall_text", "sep", "short_addr", "social_proof_text",
                "thin_sep", "web_scan_button"):
        from rugmunchbot.formatters import (  # noqa: F401
            EVM_RE, SOL_RE, TOKEN_RE, detect_chain, footer_links,
            format_scan_report, format_wallet_report, is_evm, is_owner, is_sol,
            paywall_text, sep, short_addr, social_proof_text, thin_sep, web_scan_button,
        )

        return locals()[name]
    if name in ("back_kb", "buy_stars_kb", "main_menu_kb", "paywall_kb",
                "pricing_kb", "scamschool_kb", "scan_result_kb", "social_kb",
                "topup_kb"):
        from rugmunchbot.keyboards import (  # noqa: F401
            back_kb, buy_stars_kb, main_menu_kb, paywall_kb, pricing_kb,
            scamschool_kb, scan_result_kb, social_kb, topup_kb,
        )

        return locals()[name]
    if name in ("analyze_wallet", "scan_token"):
        from rugmunchbot.scanner import analyze_wallet, scan_token  # noqa: F401

        return locals()[name]
    if name in ("check_spam", "get_sol_price"):
        from rugmunchbot.states import check_spam, get_sol_price  # noqa: F401

        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
