"""Risk verdict + consolidated Risk Signals block for the scan report.

Split out of ``formatters/_scan_report.py`` during the Batch 14
de-godding pass: the one-line verdict renderer and the "why it's sus"
enrichment section consumed by ``format_scan_report`` and the SENTINEL
deep-forensic report."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

from app.domains.telegram.rugmunchbot.formatters._base import thin_sep
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

# The consolidated rug-risk scorer's one-line verdict renderer (pure, no I/O).
# Imported defensively so the Risk Signals section still degrades gracefully
# when the scorer module is not deployable.
_risk_verdict_text: Callable[[dict[str, Any]], str] | None
try:  # pragma: no cover - exercised only when the scorer helper is absent
    from app.domains.scanner.rug_risk_score import risk_verdict_text as _risk_verdict_text
except ImportError:
    _risk_verdict_text = None

__all__ = [
    "_RISK_SIGNAL_KEYS",
    "_format_risk_signals_section",
    "_verdict_text",
]


def _verdict_text(risk: float) -> str:
    """One-line plain-language verdict for the 0-100 risk score."""
    if risk >= 80:
        return "\U0001f534 <b>EXTREME</b> \u2014 avoid unless you fully understand the risk"
    if risk >= 60:
        return "\U0001f7e1 <b>HIGH RISK</b> \u2014 significant red flags, DYOR before buying"
    if risk >= 40:
        return "\U0001f7e0 <b>MODERATE</b> \u2014 some risk signals, proceed with caution"
    if risk >= 20:
        return "\U0001f7e2 <b>LOW RISK</b> \u2014 few red flags detected"
    return "\u2705 <b>SAFE</b> \u2014 no major red flags detected"


# ── Risk Signals (consolidated "why it's sus" enrichment block) ──────────

# Pre-rendered per-module signal strings, in the order they render.
_RISK_SIGNAL_KEYS: tuple[str, ...] = (
    "bundle_signal",
    "reputation_signal",
    "wash_signal",
    "whale_signal",
    "authority_signal",
    "crossref_signal",
)


def _format_risk_signals_section(r: dict[str, Any]) -> list[str]:
    """Render the consolidated "why it's sus" block (empty when clean).

    Consumes the ``risk_signals`` dict attached by
    ``app.domains.scanner.scan_enrich.enrich_scan``::

        {
            "bundled": bool | None, "deployer_repeat": bool | None,
            "wash_trading": bool | None, "whale_flow": bool | None,
            "mintable": bool | None, "freezable": bool | None,
            "lp_locked": bool | float | None,
            "known_scammer": bool | None, "kol_handle": str | None,
            "bundle_signal": str | None,     # pre-rendered per-module lines
            "reputation_signal": str | None,
            "wash_signal": str | None, "whale_signal": str | None,
            "authority_signal": str | None, "crossref_signal": str | None,
            "rug_risk_score": {"score", "verdict", "flags", "reasons"},
            "risk_verdict_text": str,        # one-line verdict (pre-rendered)
        }

    Renders the consolidated verdict line plus every fired signal string,
    indented two spaces under a bold header (matching the other /scan
    sections). Signal strings are HTML-escaped — they come from databus /
    provider payloads and must never inject markup. Returns an empty list
    when the scan carried no ``risk_signals`` or none fired, so the report
    simply omits the section.
    """
    rs = r.get("risk_signals")
    if not isinstance(rs, dict):
        return []
    fired = [str(rs[key]) for key in _RISK_SIGNAL_KEYS if rs.get(key)]
    if not fired:
        return []

    lines: list[str] = ["", "\U0001f50d <b>Risk Signals</b>", thin_sep()]
    verdict = rs.get("risk_verdict_text")
    if not verdict and _risk_verdict_text is not None:
        try:
            verdict = _risk_verdict_text(rs.get("rug_risk_score") or {})
        except Exception as e:
            logger.debug("risk verdict render failed: %s", e)
            verdict = None
    if verdict:
        lines.append(f"  {html.escape(str(verdict))}")
    lines.extend(f"  \u26a0\ufe0f {html.escape(str(s))}" for s in fired)
    return lines
