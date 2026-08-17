"""HTML rendering helpers for the OSINT command handlers."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

from app.domains.telegram.rugmunchbot.formatters import footer_links, thin_sep
from app.domains.telegram.rugmunchbot.handlers.osint._common import (
    _CATEGORY_EMOJI,
    _CONFIDENCE_EMOJI,
    _MAX_AGGREGATE_ERRORS,
    _MAX_AGGREGATE_FINDINGS,
    _MAX_FINDINGS,
    _MAX_SITES,
    _REVERSE_SEARCH_LINKS,
    logger,
)

if TYPE_CHECKING:
    from app.osint import OsintReport
    from app.osint_tools import OsintResult


def _format_report(report: OsintReport, title: str) -> str:
    """Render an OsintReport as a compact HTML message (top findings first)."""
    parts = [f"{title}\n{thin_sep()}", f"<code>{html.escape(report.target)}</code>"]
    if report.errors:
        parts.append(f"\u26a0\ufe0f <i>{html.escape('; '.join(report.errors[:2]))}</i>")
    findings = report.findings[:_MAX_FINDINGS]
    if not findings:
        parts.append("<i>No public findings for this target.</i>")
    else:
        for f in findings:
            conf = _CONFIDENCE_EMOJI.get(f.confidence, "\U0001f7e1")
            cat = _CATEGORY_EMOJI.get(f.category, "\u2022")
            parts.append(f"{cat} {conf} <b>{html.escape(f.category.title())}</b> \u2014 {html.escape(f.summary)}")
        extra = len(report.findings) - _MAX_FINDINGS
        if extra > 0:
            parts.append(f"<i>\u2026and {extra} more</i>")
    parts.append(footer_links())
    return "\n".join(parts)


def _format_username_result(result: OsintResult) -> str:
    """Render a username lookup (found accounts first, then '+N more…')."""
    sites = [s for s in result.sites if s.exists]
    parts = ["\U0001f575\ufe0f <b>Username OSINT</b>\n" + thin_sep(), f"<code>{html.escape(result.username)}</code>"]
    if not sites:
        parts.append("<i>No public accounts found.</i>")
    else:
        parts.append(f"Found <b>{len(sites)}</b> account(s):")
        for s in sites[:_MAX_SITES]:
            url = s.url_user or s.url_main
            label = html.escape(s.site)
            if url:
                parts.append(f'\u2022 <a href="{html.escape(url, quote=True)}">{label}</a>')
            else:
                parts.append(f"\u2022 {label}")
        extra = len(sites) - _MAX_SITES
        if extra > 0:
            parts.append(f"<i>\u2026and {extra} more</i>")
    parts.append(footer_links())
    return "\n".join(parts)


def _format_face_result(count: int, warning: str | None) -> str:
    """Render /face output: face count (or a graceful notice) + reverse-search hint."""
    parts = ["\U0001f5bc\ufe0f <b>Face Analysis</b>\n" + thin_sep()]
    if warning:
        parts.append(f"\u26a0\ufe0f {html.escape(warning)}")
        parts.append("<i>Use reverse image search instead.</i>")
    elif count > 0:
        parts.append(f"Detected <b>{count}</b> face(s) in this photo.")
    else:
        parts.append("No faces detected in this photo.")
    parts.append("\U0001f50e <b>Reverse search</b> \u2014 upload this photo to:")
    for label, url in _REVERSE_SEARCH_LINKS:
        parts.append(f'\u2022 <a href="{url}">{label}</a>')
    parts.append(footer_links())
    return "\n".join(parts)


def _unavailable_text(target: str, exc: Exception) -> str:
    """Helpful message when an OSINT tool/service is unavailable."""
    detail = str(exc).strip()
    if not detail:
        detail = "The required OSINT tool is not installed on this server."
    return f"\u26a0\ufe0f <b>OSINT Unavailable</b>\n\n<code>{html.escape(target)}</code>\n\n{html.escape(detail)}"


def _error_text(target: str, exc: Exception) -> str:
    """Friendly error card for a failed lookup."""
    logger.warning("osint lookup failed for %s: %s", target, exc)
    return (
        "\u274c <b>OSINT Lookup Failed</b>\n\n"
        f"<code>{html.escape(target)}</code>\n\n"
        "Could not complete the lookup. Please try again in a moment."
    )


def _format_aggregate_result(result: dict[str, Any]) -> str:
    """Render the merged ``run_aggregate`` result as a compact HTML message.

    Shows per-module hit counts, the top findings grouped by source/category,
    then "+N more…" and a short errors note.
    """
    target = str(result.get("target") or "?")
    target_type = str(result.get("target_type") or "unknown")
    results = result.get("results") or {}
    findings = result.get("findings") or []
    errors = result.get("errors") or []

    parts = [
        f"\U0001f575\ufe0f <b>OSINT Aggregate</b> \u2014 <b>{html.escape(target_type.upper())}</b>",
        thin_sep(),
        f"<code>{html.escape(target)}</code>",
        "",
    ]

    # Per-module hit counts.
    if results:
        parts.append("\U0001f4ca <b>Modules</b>")
        for key in sorted(results):
            report = results[key]
            if not isinstance(report, dict):
                continue
            count = len(report.get("findings") or [])
            module_errors = len(report.get("errors") or [])
            label = html.escape(str(key))
            note = f"<b>{count}</b> finding(s)"
            if module_errors:
                note += f" \u00b7 {module_errors} err"
            parts.append(f"\u2022 {label}: {note}")
    else:
        parts.append("<i>No modules produced a report.</i>")
    parts.append("")

    # Top findings, grouped by source/category.
    parts.append(f"\U0001f50e <b>Findings</b> (top {_MAX_AGGREGATE_FINDINGS})")
    shown = list(findings)[:_MAX_AGGREGATE_FINDINGS]
    if not shown:
        parts.append("<i>No findings collected.</i>")
    else:
        for finding in shown:
            if not isinstance(finding, dict):
                continue
            source = html.escape(str(finding.get("source") or "source"))
            category = str(finding.get("category") or "finding")
            summary = html.escape(str(finding.get("summary") or ""))
            confidence = str(finding.get("confidence") or "medium")
            conf = _CONFIDENCE_EMOJI.get(confidence, "\U0001f7e1")
            cat = _CATEGORY_EMOJI.get(category, "\u2022")
            parts.append(f"{cat} {conf} <b>{html.escape(category.title())}</b> \u2014 {summary} <i>({source})</i>")
        extra = len(findings) - _MAX_AGGREGATE_FINDINGS
        if extra > 0:
            parts.append(f"<i>\u2026and {extra} more findings</i>")

    # Errors note.
    if errors:
        parts.append("")
        shown_errors = [html.escape(str(e)) for e in errors[:_MAX_AGGREGATE_ERRORS]]
        parts.append(f"\u26a0\ufe0f <i>{'; '.join(shown_errors)}</i>")
        extra_errors = len(errors) - _MAX_AGGREGATE_ERRORS
        if extra_errors > 0:
            parts.append(f"<i>\u2026and {extra_errors} more source errors</i>")

    parts.append(footer_links())
    return "\n".join(parts)
