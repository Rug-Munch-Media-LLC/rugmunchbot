# VENDORED 2026-08-17 from rmi-backend/app/osint/dex_activity/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""OSINT suspicious DEX activity — wash-trading detector for DEX pairs.

Package layout (mirrors :mod:`app.osint.domain` / :mod:`app.osint.face`):

* :mod:`app.osint.dex_activity._core` — public entry point
  (:func:`lookup_pair_activity`) + DexScreener fetch.
* :mod:`app.osint.dex_activity._signals` — the wash-trading signal
  heuristics + aggregate risk scoring.
* :mod:`app.osint.dex_activity._common` — shared numeric/format helpers.

Contract: ``lookup_pair_activity(pair_address, *, chain="auto", timeout=60)
-> OsintReport`` (see :mod:`app.osint`). API failures and empty results are
recorded in ``report.errors`` — this never raises.
"""

from __future__ import annotations

from rugmunchbot._vendor.osint.dex_activity._core import lookup_pair_activity

__all__ = ["lookup_pair_activity"]
