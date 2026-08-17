# VENDORED 2026-08-17 from rmi-backend/app/osint/email.py (Phase 2b — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Email breach + reputation OSINT module.

Aggregates Have I Been Pwned (breaches + pastes), emailrep.io reputation, and
a local disposable-domain check into a single :class:`OsintReport`.

Every source fails gracefully: rate limits (429), auth failures (401/403),
network errors, and malformed payloads are recorded in ``report.errors`` and
never crash the lookup. Callers can therefore always rely on the returned
report, even when zero findings were collected.

Usage::

    from rugmunchbot._vendor.osint.email import lookup_email

    report = lookup_email("victim@example.com", timeout=30)
    for finding in report.findings:
        print(finding.source, finding.category, finding.summary)
    for error in report.errors:
        print("error:", error)

Environment
-----------
- ``HIBP_API_KEY`` (optional) — sent as the ``hibp-api-key`` header. HIBP v3
  requires a key for authenticated lookups; without one the API rejects the
  request (401) or throttles it (429). Both are surfaced in ``report.errors``.
- ``EMAILREP_API_KEY`` (optional) — sent as the ``X-API-Key`` header. Basic
  emailrep.io reputation lookups work without a key.

References
----------
- HIBP API v3: https://haveibeenpwned.com/API/v3
- emailrep.io: https://emailrep.io/
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, cast
from urllib.parse import quote

from rugmunchbot._vendor.osint import OsintFinding, OsintNotAvailable, OsintReport

try:
    import httpx as _httpx
except ImportError:  # pragma: no cover — httpx is a runtime dependency of the backend
    _httpx = None  # type: ignore[assignment]

httpx: Any = _httpx

logger = logging.getLogger(__name__)

# ─── Tunables ────────────────────────────────────────────────────────

HIBP_BREACH_URL = "https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
HIBP_PASTE_URL = "https://haveibeenpwned.com/api/v3/pasteaccount/{email}"
EMAILREP_URL = "https://emailrep.io/{email}"

HIBP_API_KEY_HEADER = "hibp-api-key"
EMAILREP_API_KEY_HEADER = "X-API-Key"

USER_AGENT = "rmi-backend-osint/1.0 (+https://rugmunch.io; email breach lookup)"

#: Email sanity check — deliberately permissive (real mailboxes contain dots,
#: plus-addresses and unicode). Deliverability checks are out of scope here.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

#: Small curated set of well-known disposable / throwaway email domains used as
#: a cheap scam signal. Not exhaustive; treat a match as an indicator, not proof.
DISPOSABLE_DOMAINS: frozenset[str] = frozenset(
    {
        "10minutemail.com",
        "10minutemail.net",
        "20minutemail.com",
        "33mail.com",
        "dispostable.com",
        "emailondeck.com",
        "fakemailgenerator.com",
        "getairmail.com",
        "guerrillamail.com",
        "guerrillamail.net",
        "guerrillamail.org",
        "guerrillamailblock.com",
        "mailcatch.com",
        "maildrop.cc",
        "mailinator.com",
        "mailinator.net",
        "mailinator.org",
        "mailinator2.com",
        "mailnator.com",
        "mailnesia.com",
        "mailtemp.net",
        "mintemail.com",
        "moakt.com",
        "sharklasers.com",
        "spam4.me",
        "spamgourmet.com",
        "temp-mail.io",
        "temp-mail.org",
        "tempmail.com",
        "tempmail.net",
        "tempinbox.com",
        "throwawaymail.com",
        "trashmail.com",
        "trashmail.de",
        "trashmail.net",
        "yopmail.com",
        "yopmail.fr",
        "yopmail.net",
    }
)


# ─── Public API ──────────────────────────────────────────────────────


def lookup_email(email: str, *, timeout: int = 60) -> OsintReport:
    """Aggregate breach + reputation intelligence for ``email``.

    Each source is queried independently; failures are recorded in
    ``report.errors`` instead of raising. :class:`OsintNotAvailable` is raised
    only when the httpx runtime dependency is missing.

    Args:
        email: Email address to investigate.
        timeout: Upper bound (seconds) for the whole lookup; each network
            source is allowed a proportional share so one slow API cannot
            consume the entire budget.

    Returns:
        :class:`OsintReport` with ``target_type="email"``.
    """
    if httpx is None:  # pragma: no cover
        raise OsintNotAvailable("httpx is required for email OSINT: pip install httpx")

    email = email.strip()
    report = OsintReport(target=email, target_type="email")

    if not _EMAIL_RE.match(email):
        report.errors.append(f"invalid email address: {email!r}")
        return report

    per_source = max(5.0, min(float(timeout), 20.0))
    logger.debug("email OSINT lookup for %s (per-source timeout %.1fs)", email, per_source)

    _hibp_breaches(email, report, per_source)
    _hibp_pastes(email, report, per_source)
    _emailrep(email, report, per_source)
    _disposable_check(email, report)

    return report


# ─── Source implementations ──────────────────────────────────────────


def _hibp_breaches(email: str, report: OsintReport, timeout: float) -> None:
    """Query Have I Been Pwned for known breaches affecting ``email``."""
    url = HIBP_BREACH_URL.format(email=quote(email, safe=""))
    try:
        payload = _get_json(url, headers=_hibp_headers(), timeout=timeout, params={"truncateResponse": "false"})
    except httpx.HTTPStatusError as exc:
        _record_http_error(report, "hibp", exc)
        return
    except httpx.RequestError as exc:
        report.errors.append(f"hibp: request failed: {exc}")
        return

    for breach in _as_object_list(payload):
        _add_breach_finding(report, breach)


def _hibp_pastes(email: str, report: OsintReport, timeout: float) -> None:
    """Query Have I Been Pwned for paste exposures of ``email``."""
    url = HIBP_PASTE_URL.format(email=quote(email, safe=""))
    try:
        payload = _get_json(url, headers=_hibp_headers(), timeout=timeout)
    except httpx.HTTPStatusError as exc:
        _record_http_error(report, "hibp", exc)
        return
    except httpx.RequestError as exc:
        report.errors.append(f"hibp: request failed: {exc}")
        return

    for paste in _as_object_list(payload):
        paste_source = str(paste.get("Source") or "unknown")
        paste_date = str(paste.get("Date") or "?")
        title = str(paste.get("Title") or "untitled")
        _finding(
            report,
            source="hibp",
            category="breach",
            summary=f"email exposed in paste '{title}' from {paste_source} ({paste_date})",
            confidence="high",
            paste_id=paste.get("Id"),
            source_name=paste_source,
            title=title,
            date=paste_date,
            email_count=paste.get("EmailCount"),
        )


def _emailrep(email: str, report: OsintReport, timeout: float) -> None:
    """Query emailrep.io for reputation, leak and profile signals."""
    url = EMAILREP_URL.format(email=quote(email, safe=""))
    try:
        payload = _get_json(url, headers=_emailrep_headers(), timeout=timeout)
    except httpx.HTTPStatusError as exc:
        _record_http_error(report, "emailrep", exc)
        return
    except httpx.RequestError as exc:
        report.errors.append(f"emailrep: request failed: {exc}")
        return

    if not isinstance(payload, dict):
        report.errors.append("emailrep: unexpected response shape")
        return
    payload = cast("dict[str, Any]", payload)

    details = payload.get("details")
    details_dict = cast("dict[str, Any]", details) if isinstance(details, dict) else {}

    reputation = str(payload.get("reputation") or "unknown")
    suspicious = bool(payload.get("suspicious"))
    references = payload.get("references", 0)

    summary = f"emailrep.io reputation: {reputation}"
    if suspicious:
        summary += " (flagged suspicious)"
    _finding(
        report,
        source="emailrep",
        category="reputation",
        summary=summary,
        confidence="medium",
        reputation=reputation,
        suspicious=suspicious,
        references=references,
        blacklisted=bool(details_dict.get("blacklisted")),
        malicious_activity=bool(details_dict.get("malicious_activity")),
        spam=bool(details_dict.get("spam")),
        deliverable=bool(details_dict.get("deliverable")),
        disposable=bool(details_dict.get("disposable")),
        free_provider=bool(details_dict.get("free_provider")),
        valid_mx=bool(details_dict.get("valid_mx")),
        spoofable=bool(details_dict.get("spoofable")),
        first_seen=details_dict.get("first_seen"),
        last_seen=details_dict.get("last_seen"),
    )

    leaked = bool(details_dict.get("credentials_leaked"))
    leaked_recent = bool(details_dict.get("credentials_leaked_recent"))
    data_breach = bool(details_dict.get("data_breach"))
    if leaked or data_breach:
        leak_summary = "emailrep.io reports leaked credentials"
        if leaked_recent:
            leak_summary += " (recent)"
        _finding(
            report,
            source="emailrep",
            category="breach",
            summary=leak_summary,
            confidence="medium",
            credentials_leaked=leaked,
            credentials_leaked_recent=leaked_recent,
            data_breach=data_breach,
        )

    profiles = payload.get("profiles") or details_dict.get("profiles") or []
    if isinstance(profiles, list) and profiles:
        profile_strs = [str(profile) for profile in profiles]
        _finding(
            report,
            source="emailrep",
            category="reputation",
            summary=f"email linked to {len(profile_strs)} social profile(s): {', '.join(profile_strs[:8])}",
            confidence="medium",
            profiles=profile_strs,
        )


def _disposable_check(email: str, report: OsintReport) -> None:
    """Flag emails on known disposable domains (cheap scam signal)."""
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in DISPOSABLE_DOMAINS:
        _finding(
            report,
            source="disposable",
            category="risk",
            summary=f"email uses known disposable domain '{domain}'",
            confidence="medium",
            domain=domain,
        )


# ─── Helpers ─────────────────────────────────────────────────────────


def _add_breach_finding(report: OsintReport, breach: dict[str, Any]) -> None:
    name = str(breach.get("Name") or breach.get("Title") or "unknown")
    title = str(breach.get("Title") or name)
    domain = str(breach.get("Domain") or "?")
    breach_date = str(breach.get("BreachDate") or "?")
    pwn_count = breach.get("PwnCount")
    pwn_str = f"{int(pwn_count):,}" if isinstance(pwn_count, int) else "unknown"
    data_classes = breach.get("DataClasses")
    classes = [str(item) for item in data_classes] if isinstance(data_classes, list) else []

    summary = f"email in breach '{title}' ({domain}, {breach_date}) — {pwn_str} accounts"
    if classes:
        summary += f", data classes: {', '.join(classes[:6])}"

    _finding(
        report,
        source="hibp",
        category="breach",
        summary=summary,
        confidence="high",
        breach_name=name,
        title=title,
        domain=domain,
        breach_date=breach_date,
        pwn_count=pwn_count,
        data_classes=classes,
        verified=bool(breach.get("IsVerified")),
        fabricated=bool(breach.get("IsFabricated")),
        sensitive=bool(breach.get("IsSensitive")),
        spam_list=bool(breach.get("IsSpamList")),
    )


def _finding(
    report: OsintReport,
    *,
    source: str,
    category: str,
    summary: str,
    confidence: str = "medium",
    **data: Any,
) -> None:
    """Map a resolved fact to an :class:`OsintFinding` and append it."""
    report.findings.append(
        OsintFinding(source=source, category=category, summary=summary, confidence=confidence, data=dict(data))
    )


def _hibp_headers() -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    api_key = os.getenv("HIBP_API_KEY", "").strip()
    if api_key:
        headers[HIBP_API_KEY_HEADER] = api_key
    return headers


def _emailrep_headers() -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    api_key = os.getenv("EMAILREP_API_KEY", "").strip()
    if api_key:
        headers[EMAILREP_API_KEY_HEADER] = api_key
    return headers


def _get_json(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    params: dict[str, str] | None = None,
) -> Any:
    """GET ``url`` and return decoded JSON.

    Raises:
        httpx.HTTPStatusError: Non-2xx response.
        httpx.RequestError: Network / timeout failure.
    """
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def _as_object_list(payload: Any) -> list[dict[str, Any]]:
    """Coerce a JSON payload into a list of objects (defensive)."""
    if not isinstance(payload, list):
        return []
    return [cast("dict[str, Any]", item) for item in payload if isinstance(item, dict)]


def _record_http_error(report: OsintReport, source: str, exc: httpx.HTTPStatusError) -> None:
    """Map an HTTP error to a report error (silencing benign 404s)."""
    status = exc.response.status_code
    if status == 404:
        return  # "not found" = no records for this account
    if status == 429:
        report.errors.append(f"{source}: rate limited (429) — add an API key or retry later")
        return
    if status in (401, 403):
        report.errors.append(f"{source}: access denied ({status}) — check API key / permissions")
        return
    report.errors.append(f"{source}: HTTP {status} — {exc}")
