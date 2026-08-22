"""Shared pure text helpers for the scan-report / SENTINEL formatters.

Split out of ``formatters/_scan_report.py`` during the Batch 14
de-godding pass; modularized into a package (2026-08-21). This ``__init__``
is a pure re-export shim: every name and signature is unchanged, and
sibling formatters keep importing ``_scan_report_common`` exactly as before.

Also hosts the canonical scan-report LAYOUT helpers: every scan report —
regular ``/scan``, ``/scandeep``, ``/sentinelscan`` and the ML ``/deepscan``
— shares the same 4-part skeleton:

  1. one-line VERDICT up top
  2. key metrics as a tight bold table
  3. evidence bullets
  4. a short "what to watch" line

Layout:
  * ``_text.py``        — generic string utils
  * ``_fields.py``      — report-field extractors (never fabricate values)
  * ``_layout.py``      — key-metrics table, verdict rows, watch line
  * ``_scam_matrix.py`` — consolidated Scam Signals risk matrix
"""

from __future__ import annotations

from rugmunchbot.formatters._scan_report_common._fields import (
    _bundle_wallet_count as _bundle_wallet_count,
    _cex_funded_pct as _cex_funded_pct,
    _fake_volume_pct as _fake_volume_pct,
    _float_or_none as _float_or_none,
    _freeze_auth as _freeze_auth,
    _fresh_wallet_pct as _fresh_wallet_pct,
    _holders_count as _holders_count,
    _lp_lock_pct as _lp_lock_pct,
    _mev_note as _mev_note,
    _mint_auth as _mint_auth,
    _tax_pct_str as _tax_pct_str,
    _token_2022_flag as _token_2022_flag,
    _volume_up_price_flat_churn as _volume_up_price_flat_churn,
)
from rugmunchbot.formatters._scan_report_common._layout import (
    _RISK_HIGH as _RISK_HIGH,
    _RISK_MID as _RISK_MID,
    _THIN_SEP as _THIN_SEP,
    _bundle_verdict_row as _bundle_verdict_row,
    _distribution_verdict as _distribution_verdict,
    _fmt_bool_emoji as _fmt_bool_emoji,
    _fmt_market_value as _fmt_market_value,
    _fmt_num as _fmt_num,
    _holder_distribution_rows as _holder_distribution_rows,
    _key_metrics_rows as _key_metrics_rows,
    _launch_intel_rows as _launch_intel_rows,
    _risk_emoji as _risk_emoji,
    _risk_level_word as _risk_level_word,
    _what_to_watch_line as _what_to_watch_line,
)
from rugmunchbot.formatters._scan_report_common._scam_matrix import (
    _SCAM_SIGNAL_GREEN as _SCAM_SIGNAL_GREEN,
    _SCAM_SIGNAL_RED as _SCAM_SIGNAL_RED,
    _SCAM_SIGNAL_UNKNOWN as _SCAM_SIGNAL_UNKNOWN,
    _evidence_security as _evidence_security,
    _format_scam_signals_section as _format_scam_signals_section,
    _honeypot_flat as _honeypot_flat,
    _risk_flag_set as _risk_flag_set,
    _scam_signal_rows as _scam_signal_rows,
)
from rugmunchbot.formatters._scan_report_common._text import (
    _clip as _clip,
    _collapse_blank_lines as _collapse_blank_lines,
    _short_addr as _short_addr,
)

__all__ = [
    "_SCAM_SIGNAL_GREEN",
    "_SCAM_SIGNAL_RED",
    "_SCAM_SIGNAL_UNKNOWN",
    "_bundle_verdict_row",
    "_bundle_wallet_count",
    "_cex_funded_pct",
    "_clip",
    "_collapse_blank_lines",
    "_distribution_verdict",
    "_evidence_security",
    "_fake_volume_pct",
    "_fake_volume_verdict_text",
    "_float_or_none",
    "_fmt_bool_emoji",
    "_fmt_market_value",
    "_fmt_num",
    "_format_scam_signals_section",
    "_freeze_auth",
    "_fresh_wallet_pct",
    "_holder_distribution_rows",
    "_holders_count",
    "_honeypot_flat",
    "_key_metrics_rows",
    "_launch_intel_rows",
    "_lp_lock_pct",
    "_mev_note",
    "_mint_auth",
    "_risk_emoji",
    "_risk_flag_set",
    "_risk_level_word",
    "_scam_signal_rows",
    "_short_addr",
    "_tax_pct_str",
    "_token_2022_flag",
    "_volume_up_price_flat_churn",
    "_what_to_watch_line",
]
