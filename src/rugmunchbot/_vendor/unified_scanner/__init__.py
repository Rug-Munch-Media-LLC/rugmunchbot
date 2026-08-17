# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/unified/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Unified Token Scanner package — split from unified_token_scanner.py.

Single source of truth for ALL token scanning at RMI.
"""

from rugmunchbot._vendor.unified_scanner.model import ScanResult
from rugmunchbot._vendor.unified_scanner.scanner import UnifiedTokenScanner

_scanner: UnifiedTokenScanner | None = None


def get_token_scanner() -> UnifiedTokenScanner:
    global _scanner
    if _scanner is None:
        _scanner = UnifiedTokenScanner()
    return _scanner


__all__ = [
    "ScanResult",
    "UnifiedTokenScanner",
    "get_token_scanner",
]
