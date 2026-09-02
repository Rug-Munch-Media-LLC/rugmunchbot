"""Risk Report section for the scan report — the full product demo (package).

Split out of ``formatters/_scan_report_riskreport.py`` during the de-godding
pass. This ``__init__`` is a pure re-export shim: every public name and
signature is unchanged, and sibling formatters keep importing
``formatters._scan_report_riskreport`` exactly as before.

Wires the structured RiskReport data model from
``rugmunch_scanner/unified/risk_report.py`` into the bot's scan formatter:

  deployer / holders / first-buyer / top-25 / fresh% / CEX-funded% /
  bundle / cluster / honeypot / liquidity + the ~30-factor risk score +
  rug/scam determination.

Two data paths, one renderer:

* ``r["risk_report"]`` — the serialized RiskReport dict (``RiskReport.to_dict``)
  when the backend's scanner wired the phase. Rendered as-is.
* Otherwise ``_build_risk_report_from_scan(r)`` synthesizes the SAME dict
  shape from the scan payload the bot already carries (security evidence,
  SENTINEL modules, bundle analysis, wallet metrics, GMGN holders). The
  factor keys / labels / weights / bands mirror the canonical scanner module
  exactly — keep them in sync when ``rugmunch_scanner/unified/risk_report.py``
  changes (see the provenance note on each mirror table).

Rendering contract (Telegram HTML, fail-open):

* Basic scans (``deep=False``) — CONCISE: composite score + verdict +
  coverage + one line per section + the top critical/risk factors.
* Deeper scans (``deep=True``) — FULL: the same headline, the complete
  ~30-factor table and the deeper-level narrative, with the AI-assisted
  marker when an LLM pass ran.

Pure module: no network, no I/O; every missing value renders "—"/unknown,
never a fabricated number.

Layout:
  * ``_constants.py``    — canonical mirror tables + coercion helpers
  * ``_extract.py``      — per-section extraction (mirrors risk_report._extract_*)
  * ``_factors.py``      — ~30-factor construction (mirrors risk_report._factor)
  * ``_determination.py``— composite + rug/scam determination
  * ``_builder.py``      — public RiskReport dict builder + deeper analysis
  * ``_render.py``       — Telegram-HTML renderer
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._base import thin_sep
from rugmunchbot.formatters._scan_report_riskreport._builder import (
    _build_risk_report_from_scan as _build_risk_report_from_scan,
)
from rugmunchbot.formatters._scan_report_riskreport._constants import (
    _MIN_KNOWN_FOR_DETERMINATION as _MIN_KNOWN_FOR_DETERMINATION,
)
from rugmunchbot.formatters._scan_report_riskreport._render import (
    _render_risk_report as _render_risk_report,
)

# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════════


def _concise_report_postable(rep: dict[str, Any]) -> bool:
    """True when a CONCISE (``deep=False``) report is substantive enough to
    render a composite verdict.

    Scans-of-interest fix (2026-08-29): a nearly-empty report renders the
    self-contradictory headline "Composite Risk 0/100 OK · 1/30 factors
    known" while the card header claims a different score — a reader learns
    nothing. The concise block only renders when it can actually declare a
    verdict: >= :data:`_MIN_KNOWN_FOR_DETERMINATION` known factors (the same
    threshold the scanner uses to emit a determination) OR a decisive scam is
    confirmed (a confirmed honeypot matters even on a thin scan). Pure —
    never raises.
    """
    dq = rep.get("data_quality") or {}
    try:
        known = int(dq.get("factors_known") or 0)
    except (TypeError, ValueError):
        known = 0
    if known >= _MIN_KNOWN_FOR_DETERMINATION:
        return True
    return str(rep.get("determination") or "") == "scam"


def format_risk_report_block(r: dict[str, Any], *, deep: bool = False) -> list[str]:
    """Risk Report section for the scan report ([] when the scan has no data).

    Uses the backend-provided RiskReport dict when the payload carries one;
    otherwise builds the same shape from the scan fields (see
    ``_build_risk_report_from_scan``). Basic scans render the concise block;
    deep scans render the full ~30-factor table + deeper analysis. The
    concise block is skipped entirely when the report is too thin to declare
    a verdict (see :func:`_concise_report_postable`) — a 1/30-factor
    composite is a fabrication, not a finding.
    """
    raw = r.get("risk_report")
    rep = raw if isinstance(raw, dict) else _build_risk_report_from_scan(r, deep=deep)
    if not isinstance(rep, dict) or not rep.get("factors"):
        return []
    if not deep and not _concise_report_postable(rep):
        return []
    # AI-assisted marker: when the deep scan ran the LLM verdict pass
    # (``ai_verdict``), flag the locally-built report so the renderer notes
    # the deeper explanation is AI-assisted (backend reports carry their own
    # ``ai_assisted`` flag).
    if deep and not rep.get("ai_assisted") and isinstance(r.get("ai_verdict"), str):
        rep = {**rep, "ai_assisted": True}
    title = "Risk Report \u2014 30-factor breakdown" if deep else "Risk Report"
    return ["", f"\U0001f3af <b>{title}</b>", thin_sep(), *_render_risk_report(rep, deep=deep)]


__all__ = [
    "_build_risk_report_from_scan",
    "format_risk_report_block",
]
