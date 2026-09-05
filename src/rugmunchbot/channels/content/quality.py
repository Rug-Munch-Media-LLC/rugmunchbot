"""Suspect-scan churn rail (2026-08-29, scans-channel content fix).

Problem: the launch-alert rail and scan-history rail BOTH come from the
backend and are frequently empty (on 2026-08-29 both returned ``[]``), so
@rmiscans posted nothing. The churn rail instead GENERATES scan content:
it pulls the scanner's flagged honeypots/rugs (``_scanner_candidates()`` —
our own scanner-flagged honeypots/rugs plus sketchy fresh launches),
runs the full unified v2 scanner on the top candidates, and normalizes
each result into the same scan-note shape ``format_scan_card`` renders
(with the complete RiskReport block: deployer, top-25, first-buyer,
fresh%, CEX-funded%, bundle/cluster, 30-factor, determination).

Honesty gate (2026-08-29 scans-of-interest fix): a token is only ever
posted as a FULL card when the scan produced a real verdict AND the
actual signal (holders / liquidity / volume / first-buyers / bundle+
deployer flags). A scan that failed or timed out is DROPPED — a sketch-
only "unscanned · thin_liquidity" note carries no why. A scan whose deep
phases are still running posts a ``scan_pending`` "Scanning…" teaser and
the full card later (never "Deep analysis unavailable").

Note: ``_churn_one_scan`` is resolved through the package namespace
(``_pkg``) so tests that monkeypatch ``rugmunchbot.channels.content``
attributes keep working after the split into sub-modules.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Self-package reference: lets tests that monkeypatch attributes on
# ``rugmunchbot.channels.content`` (the package) affect the calls below.
from rugmunchbot.channels import content as _pkg
from rugmunchbot.channels.content.formatters import _scan_score_line
from rugmunchbot.channels.content.helpers import logger
from rugmunchbot.formatters._scan_report_common._fields import _float_or_none, _holders_count
from rugmunchbot.formatters._scan_report_riskreport import _build_risk_report_from_scan

_SCAN_CHURN_CONCURRENCY = 2
_SCAN_CHURN_SCAN_TIMEOUT = 45.0
# Bounded completion poll after the fast-path POST returns (~25s) — the
# backend's deep phases (holders/bundle/first-buyers/wallet metrics) land on
# the status endpoint seconds later. Kept well under the per-scan budget so
# the churn rail still finishes inside the outer gather timeout.
_SCAN_CHURN_POLL_MAX_S = 12.0

#: Minimum actual-signal fields a churn note must carry to post as a FULL
#: "scan of interest" card (holders count / liquidity amount / volume /
#: first-25-buyers / bundle+deployer flags). Below this the card would show
#: no real why — the token is either still scanning (teaser) or dropped.
_SCAN_CHURN_MIN_SIGNAL = 2

__all__ = [
    "_SCAN_CHURN_MIN_SIGNAL",
    "_churn_one_scan",
    "_scan_signal_count",
    "_score_risk_level",
    "fetch_suspect_scan_notes",
]


def _score_risk_level(score: Any) -> str:
    """Risk word (high/medium/low) consistent with the card's score band.

    Mirrors the layout helper's bands (HIGH ≥ 70, MEDIUM ≥ 40, LOW below) so
    the card header's label always agrees with the number next to it — the
    header and body can never contradict each other on the level. Pure —
    never raises.
    """
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if value >= 70.0:
        return "high"
    if value >= 40.0:
        return "medium"
    return "low"


def _scan_signal_count(note: dict[str, Any]) -> int:
    """How many of the five actual-signal fields a scan note carries.

    The scans-of-interest card must show the REAL why a token is risky:
    holders count, liquidity amount, volume, first-25-buyers, bundle/deployer
    flags. Counts how many of those the scan payload actually reports — a
    provider that did not report a field never counts. Pure — never raises.
    """
    count = 0
    if _holders_count(note) is not None:
        count += 1
    if _float_or_none(note.get("liquidity_usd") or note.get("liquidity")) is not None:
        count += 1
    if _float_or_none(note.get("volume_24h")) is not None:
        count += 1
    first_buyers = note.get("first_buyers")
    if (isinstance(first_buyers, list) and first_buyers) or (
        isinstance(first_buyers, dict) and first_buyers.get("buyers")
    ):
        count += 1
    if note.get("bundle_analysis") or note.get("deployer_profile") or note.get("deployer_address"):
        count += 1
    return count


async def _churn_one_scan(
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    """Run the full unified v2 scanner on one suspicious candidate.

    Returns the normalized scan note (``churned=True``) carrying the COMPLETE
    v2 payload so the scans card renders the real why — holders, liquidity,
    volume, first-buyers, bundle/deployer flags, RiskReport. Scans-of-interest
    honesty gate (2026-08-29):

    * Scan FAILED / timed out → ``None``. A token with no scan output is
      never posted as a "scan of interest" — it either gets scanned or gets
      dropped, never a sketch-only guess.
    * Deep phases still running after the bounded poll AND the fast path does
      not yet carry enough signal → a ``scan_pending`` note: the card reads
      "Scanning… deep analysis in progress — will repost when ready", never
      "Deep analysis unavailable". The poster keys its dedupe separately so
      the full card still posts when the deep data lands (re-post later).
    * Scan produced a real verdict + signal → the full note, with the card's
      risk score reconciled to the RiskReport composite so the header and the
      body block always agree.

    Fail-open by contract: never raises.
    """
    address = str(candidate.get("address") or candidate.get("token_address") or "").strip()
    if not address:
        return None
    chain = str(candidate.get("chain") or "solana").strip()
    try:
        from rugmunchbot.tools.scanner._scans import scan_token_v2

        data = await asyncio.wait_for(
            scan_token_v2(address, chain), timeout=_SCAN_CHURN_SCAN_TIMEOUT
        )
    except Exception as exc:  # fail-open — drop, never post sketch-only data
        logger.debug("channels:scan_churn_failed addr=%.40s err=%s", address, str(exc)[:80])
        return None
    if not isinstance(data, dict) or data.get("status") != "ok":
        return None
    # Normalize identity keys so every downstream reader (RiskReport builder,
    # card formatters, dedupe) finds the address regardless of the backend
    # payload shape (some v2 responses only carry ``token_address``).
    if not data.get("address"):
        data["address"] = address
    if not data.get("token_address"):
        data["token_address"] = address

    # ── Deep-analysis completion merge (2026-08-29 fix) ──
    # The v2 engine returns within ~25s while its background phases (holder
    # data, wallet metrics, bundle detection, first-buyers, SENTINEL,
    # RiskReport) continue and land on the status endpoint. Without the
    # poll+merge the churn card only ever sees the fast path and renders
    # "Deep analysis (deployer/holders/bundles): unavailable" with ~1/30
    # factors known. Same bounded-poll contract as /scan — fail-open: on
    # timeout the note is marked ``scan_pending`` (never "unavailable").
    deep_done = data.get("background_status") != "running"
    if data.get("background_status") == "running":
        try:
            from rugmunchbot.handlers._scan_handlers._scan import _poll_v2_scan_completion
            from rugmunchbot.handlers._scanning_helpers import _merge_completed_scan

            completed = await _poll_v2_scan_completion(
                address,
                chain,
                max_wait_s=_SCAN_CHURN_POLL_MAX_S,
                head_start_s=0.0,
                interval_s=2.5,
            )
            if completed:
                _merge_completed_scan(data, completed)
                deep_done = True
        except Exception as exc:  # fail-open by contract
            logger.debug(
                "channels:scan_churn_poll_failed addr=%.40s err=%s", address, str(exc)[:80]
            )

    # Merge the candidate's own sketch flags (cheap, already computed) with
    # the scan's risk flags so a quiet scanner still shows the sketch signal.
    # A real scan REPLACES "unscanned" — never post a scanned token as
    # unscanned.
    flags = [str(f) for f in (data.get("risk_flags") or [])]
    for f in candidate.get("risk_flags") or []:
        f = str(f)
        if f.lower() == "unscanned":
            continue
        if f not in flags:
            flags.append(f)
    name = str(data.get("name") or candidate.get("name") or "Unknown").strip()
    symbol = str(data.get("symbol") or candidate.get("symbol") or "???").strip()

    # Reconcile the card header with the body RiskReport composite (rule:
    # header risk score and body composite MUST agree). The composite is only
    # authoritative once the deep scan completed; before that the fast-path
    # safety inversion is the honest "initial pass" score for a pending card.
    safety = data.get("safety_score")
    fast_risk = round(100.0 - float(safety), 1) if isinstance(safety, (int, float)) else None
    try:
        report = _build_risk_report_from_scan(data)
    except Exception:  # fail-open — the gate below still drops unsupported notes
        report = None
    composite = report.get("composite_score") if isinstance(report, dict) else None
    risk_score = composite if composite is not None and deep_done else fast_risk
    risk_level = _score_risk_level(risk_score)

    # The note carries the FULL v2 payload (``dict(data)``) so every provider
    # field — honeypot, risk_signals, volume_24h, gmgn, onchain, first_buyers,
    # bundle_analysis, evidence — flows into the card formatters; normalized
    # keys are overridden below.
    note = dict(data)
    note.update(
        {
            "address": address,
            "token_address": address,
            "name": name,
            "symbol": symbol,
            "chain": chain,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "risk_flags": flags[:8],
            "scanned_at": str(data.get("scanned_at") or "").strip(),
            "title": name,
            "alert_type": "scan",
            "condition": _scan_score_line({"risk_score": risk_score, "risk_level": risk_level}),
            "image_url": "",
            # Churn-rail marker: the scans poster lets these post below the
            # news-importance floor (they are sus-token cards by construction).
            "churned": True,
        }
    )

    # ── Scans-of-interest gate ──
    # Post a FULL card only when the scan produced a real verdict AND the
    # actual signal (holders/liquidity/volume/first-buyers/bundle+deployer).
    # Otherwise: deep still running → "scanning…" teaser (re-post later);
    # deep finished but no signal → drop (rule: never post where we know
    # nothing).
    if composite is not None and _scan_signal_count(note) >= _SCAN_CHURN_MIN_SIGNAL:
        return note
    if not deep_done:
        note["scan_pending"] = True
        return note
    logger.debug(
        "channels:scan_churn_thin addr=%.40s composite=%s signal=%d",
        address,
        composite,
        _scan_signal_count(note),
    )
    return None


async def fetch_suspect_scan_notes(limit: int = 4) -> list[dict[str, Any]]:
    """Churn suspicious tokens through the full scanner → scan notes.

    Candidate source: ``_scanner_candidates()`` — ONLY our scanner's flagged
    honeypots/rugs (from /api/v1/scanner/recent). The top candidates are
    scanned with the unified v2 scanner (bounded concurrency, hard timeout,
    fail-open), and each successful result becomes a scan note carrying the
    FULL RiskReport payload so the @rmiscans card shows the complete output
    (deployer, top-25, first-buyer, fresh%, CEX-funded%, bundle/cluster,
    30-factor, determination) + links to the bot / website / other channels
    (``format_scan_card``).

    Returns ``[]`` when the candidate source or the scanner is down — the
    channel then fails open and posts nothing. Never raises.
    """
    if limit < 1:
        return []
    try:
        from rugmunchbot.tools.trend_algo._candidates import _scanner_candidates

        candidates = await _scanner_candidates()
    except Exception as exc:  # fail-open by contract
        logger.debug("channels:scan_churn_candidates_failed err=%s", str(exc)[:100])
        return []
    if not candidates:
        return []

    sem = asyncio.Semaphore(_SCAN_CHURN_CONCURRENCY)

    async def _bounded(candidate: dict[str, Any]) -> dict[str, Any] | None:
        async with sem:
            # Resolved through the package namespace so tests that monkeypatch
            # ``rugmunchbot.channels.content._churn_one_scan`` keep working.
            return await _pkg._churn_one_scan(candidate)

    # Outer budget must cover EVERY batch (concurrency 2, up to limit scans,
    # each bounded by the per-scan timeout): ceil(limit/2) batches at 45s + a
    # small buffer. The old fixed +15s was shorter than two sequential
    # batches, so the whole churn aborted empty before the fallback ran.
    import math

    outer_timeout = (
        math.ceil(max(1, min(len(candidates), limit)) / max(1, _SCAN_CHURN_CONCURRENCY))
        * _SCAN_CHURN_SCAN_TIMEOUT
        + 15.0
    )
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(_bounded(c) for c in candidates[:limit]), return_exceptions=True),
            timeout=outer_timeout,
        )
    except Exception as exc:  # fail-open by contract
        logger.debug("channels:scan_churn_timeout err=%s", str(exc)[:100])
        return []
    notes = [r for r in results if isinstance(r, dict)]
    return notes[: max(1, limit)]
