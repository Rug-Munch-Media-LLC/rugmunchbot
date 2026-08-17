#!/usr/bin/env python3
"""
RugMunchBot -- Scanning engines (THIN SHIM — canonical engine moved).

The canonical scanner engine now lives in ``tools/scanner.py``. This module
re-exports the public scanner API for backward compatibility: every function
delegates to the canonical implementation, so /scan, the chat scanner tool
(``scanner_intel``), wallet/investigate/payments handlers and the scan-to-RAG
hooks all share ONE scanner engine — one ``POST /api/v2/scanner/token/scan``
client, one payload shape (unified 2026-08-15; previously this module and
``tools/scanner.py`` each owned a separate v2 scan client + shape, which
allowed behavior drift).

Keep this module as a pure re-export shim — do not add scan logic here.
"""

from app.domains.telegram.rugmunchbot.tools.scanner import (
    analyze_wallet,
    analyze_wallet_v2,
    fetch_deployer_fingerprint,
    fetch_hype_score,
    scan_token,
    scan_token_v2,
    sentinel_scan,
    v2_token_to_legacy,
    v2_wallet_to_legacy,
)

__all__ = [
    "analyze_wallet",
    "analyze_wallet_v2",
    "fetch_deployer_fingerprint",
    "fetch_hype_score",
    "scan_token",
    "scan_token_v2",
    "sentinel_scan",
    "v2_token_to_legacy",
    "v2_wallet_to_legacy",
]
