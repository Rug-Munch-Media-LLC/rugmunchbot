"""SENTINEL deep-forensic scan report (package).

Split out of ``formatters/_scan_report.py`` during the Batch 14 de-godding
pass; modularized into a package (2026-08-21). This ``__init__`` is a pure
re-export shim: every public name and signature is unchanged.

Canonical advanced layout: every deep-scan report shares the scan-report
skeleton — one-line verdict up top, key metrics as a tight bold table,
evidence bullets, and a short "what to watch" line. The ML deep scan
(``handlers/deepscan.py``) delegates to ``format_deep_ml_scan_report``
here so there is exactly ONE deep-scan formatter path.

Layout:
  * ``_ml.py``       — ML deep-scan report (/deepscan)
  * ``_analyst.py``  — AI "Analyst Read" bundle/cluster interpretation
  * ``_sentinel.py`` — SENTINEL deep-forensic section + standalone report
"""

from __future__ import annotations

from rugmunchbot.formatters._scan_report_deepscan._analyst import (
    _ANALYST_READ_MAX_CHARS as _ANALYST_READ_MAX_CHARS,
    _ANALYST_READ_SYSTEM as _ANALYST_READ_SYSTEM,
    _ANALYST_READ_TIMEOUT as _ANALYST_READ_TIMEOUT,
    _as_dict as _as_dict,
    _build_bundle_intel as _build_bundle_intel,
    _safe_float as _safe_float,
    _safe_int as _safe_int,
    render_analyst_read_block as render_analyst_read_block,
)
from rugmunchbot.formatters._scan_report_deepscan._ml import (
    _FINDING_EMOJI as _FINDING_EMOJI,
    _LEVEL_RANK as _LEVEL_RANK,
    _MAX_FINDINGS as _MAX_FINDINGS,
    _RISK_EMOJI as _RISK_EMOJI,
    _fmt_score as _fmt_score,
    _fmt_tax as _fmt_tax,
    _ml_verdict_sentence as _ml_verdict_sentence,
    _ml_what_to_watch as _ml_what_to_watch,
    format_deep_ml_scan_report as format_deep_ml_scan_report,
)
from rugmunchbot.formatters._scan_report_deepscan._sentinel import (
    _sentinel_watch_line as _sentinel_watch_line,
    format_sentinel_deepscan_section as format_sentinel_deepscan_section,
    format_sentinel_scan_report as format_sentinel_scan_report,
)

__all__ = [
    "format_deep_ml_scan_report",
    "format_sentinel_deepscan_section",
    "format_sentinel_scan_report",
    "render_analyst_read_block",
]
