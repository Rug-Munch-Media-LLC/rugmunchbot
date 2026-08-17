# VENDORED 2026-08-17 from rmi-backend/app/osint/phone.py (Phase 2b — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Phone number OSINT — validation, carrier, region, timezone, line-type risk,
plus an optional phone-to-social-account registration check.

Contract (see ``app.osint``): ``lookup_phone(phone, *, timeout=30) -> OsintReport``.

Sources:
  * libphonenumber (``phonenumbers``) — validation / carrier / region /
    timezone / line-type risk. Pure Python, no network calls.
  * Ignorant (https://github.com/megadose/ignorant) — OPTIONAL supplemental
    check for which social platforms (Amazon / Instagram / Snapchat) the phone
    number is registered to. HTTP-only since v1.2 (no Telegram API session
    required). A missing ``ignorant`` package is recorded in ``report.errors``
    and the rest of the lookup still succeeds. GPL-3.0 licensed; install with
    ``pip install ignorant==1.2``.

Error handling:
  * Missing dependency  -> raises :class:`OsintNotAvailable` for ``phonenumbers``;
    the optional ``ignorant`` source degrades to a ``report.errors`` line instead.
  * Unparseable input   -> does NOT raise: returns a report with a single
    low-confidence "invalid" finding plus an entry in ``report.errors``.
  * Parsed but invalid  -> returns findings with the validity status.

Scam-risk flag: VOIP and toll-free line types are surfaced as a low-confidence
risk finding (both are common vectors for spoofed/scam calling).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from rugmunchbot._vendor.osint import OsintFinding, OsintReport

try:
    import phonenumbers
    from phonenumbers import PhoneNumberType, carrier, geocoder, timezone
except ImportError as exc:  # pragma: no cover
    from rugmunchbot._vendor.osint import OsintNotAvailable

    raise OsintNotAvailable("phonenumbers is not installed; run: pip install phonenumbers") from exc

try:
    import httpx as _httpx
except ImportError:  # pragma: no cover — httpx is a runtime dependency of the backend
    _httpx = None  # type: ignore[assignment]

httpx: Any = _httpx

# ─── Optional supplemental source: Ignorant ────────────────────────────────
# https://github.com/megadose/ignorant — checks which social platforms a phone
# number is registered to (Amazon / Instagram / Snapchat) via public sign-in /
# lookup endpoints. HTTP-only since v1.2 (no Telegram API session required).
# GPL-3.0; results are unauthenticated signals — medium confidence at best.
try:
    from ignorant.modules.shopping.amazon import amazon as _ignorant_amazon
    from ignorant.modules.social_media.instagram import instagram as _ignorant_instagram
    from ignorant.modules.social_media.snapchat import snapchat as _ignorant_snapchat

    _IGNORANT_CHECKS: tuple[tuple[str, str, Any], ...] = (
        ("amazon", "amazon.com", _ignorant_amazon),
        ("instagram", "instagram.com", _ignorant_instagram),
        ("snapchat", "snapchat.com", _ignorant_snapchat),
    )
except ImportError:  # pragma: no cover — optional source; dependency absent
    _IGNORANT_CHECKS = ()

logger = logging.getLogger(__name__)

__all__ = ["lookup_phone"]

# Human-readable labels for libphonenumber's PhoneNumberType enum values.
_LINE_TYPE_NAMES: dict[int, str] = {
    PhoneNumberType.FIXED_LINE: "fixed line",
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed line or mobile",
    PhoneNumberType.TOLL_FREE: "toll-free",
    PhoneNumberType.PREMIUM_RATE: "premium rate",
    PhoneNumberType.SHARED_COST: "shared cost",
    PhoneNumberType.VOIP: "voip",
    PhoneNumberType.PERSONAL_NUMBER: "personal number",
    PhoneNumberType.PAGER: "pager",
    PhoneNumberType.UAN: "universal access number",
    PhoneNumberType.VOICEMAIL: "voicemail",
    PhoneNumberType.UNKNOWN: "unknown",
}

# Line types that are common scam-calling vectors (spoofing / burner lines).
_SCAM_RISK_TYPES: frozenset[int] = frozenset({PhoneNumberType.VOIP, PhoneNumberType.TOLL_FREE})


def lookup_phone(phone: str, *, timeout: int = 30) -> OsintReport:
    """Look up validation, carrier, region, timezone, line-type risk, and
    (optionally) which social platforms the number is registered to.

    Args:
        phone: E.164 or national-format phone number (e.g. "+14155552671").
        timeout: Upper bound (seconds) for the optional phone-to-social-account
            network check; each platform probe is capped at a fraction of this.
            The libphonenumber portion performs no network I/O.

    Returns:
        An :class:`OsintReport` with findings in the ``validation``, ``region``,
        ``carrier``, and (optionally) ``risk`` categories from ``libphonenumber``,
        plus ``account`` findings from Ignorant when the optional source is
        available and the number is valid.

    Raises:
        OsintNotAvailable: if the ``phonenumbers`` package is not installed.
    """
    report = OsintReport(target=phone, target_type="phone")

    try:
        number = phonenumbers.parse(phone, None)
    except phonenumbers.NumberParseException as exc:
        report.errors.append(f"unparseable phone number: {exc}")
        report.findings.append(
            OsintFinding(
                source="libphonenumber",
                category="validation",
                summary="Invalid phone number: could not be parsed",
                confidence="low",
                data={"raw": phone, "reason": str(exc)},
            )
        )
        return report

    valid = phonenumbers.is_valid_number(number)
    possible = phonenumbers.is_possible_number(number)
    if not valid:
        report.errors.append("phone number failed libphonenumber validity check")

    e164 = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    international = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    line_type = phonenumbers.number_type(number)
    line_type_name = _LINE_TYPE_NAMES.get(line_type, "unknown")

    # -- validation -------------------------------------------------------
    if valid:
        summary = f"Valid phone number: {international}"
        confidence = "high"
    elif possible:
        summary = f"Possible but not fully valid phone number: {international}"
        confidence = "medium"
    else:
        summary = f"Invalid phone number: {international}"
        confidence = "low"
    report.findings.append(
        OsintFinding(
            source="libphonenumber",
            category="validation",
            summary=summary,
            confidence=confidence,
            data={
                "e164": e164,
                "international": international,
                "country_code": number.country_code,
                "national_number": str(number.national_number),
                "valid": valid,
                "possible": possible,
                "line_type": line_type_name,
            },
        )
    )

    # -- region / timezone -------------------------------------------------
    region_code = phonenumbers.region_code_for_number(number) or ""
    region_description = geocoder.description_for_number(number, "en") or ""
    timezones = list(timezone.time_zones_for_number(number))
    if region_code:
        summary = f"Region: {region_description or region_code} ({region_code})"
        confidence = "high"
    else:
        summary = "Region: unknown"
        confidence = "low"
    report.findings.append(
        OsintFinding(
            source="libphonenumber",
            category="region",
            summary=summary,
            confidence=confidence,
            data={
                "region_code": region_code,
                "region_description": region_description,
                "timezones": timezones,
            },
        )
    )

    # -- carrier ------------------------------------------------------------
    carrier_name = carrier.name_for_number(number, "en") or ""
    if carrier_name:
        report.findings.append(
            OsintFinding(
                source="libphonenumber",
                category="carrier",
                summary=f"Carrier: {carrier_name}",
                confidence="high",
                data={"carrier": carrier_name},
            )
        )

    # -- scam-risk flag (VOIP / toll-free) ----------------------------------
    if line_type in _SCAM_RISK_TYPES:
        report.findings.append(
            OsintFinding(
                source="libphonenumber",
                category="risk",
                summary=(
                    f"{line_type_name.title()} number — common scam-calling vector; "
                    "verify the caller's identity independently"
                ),
                confidence="low",
                data={"line_type": line_type_name, "scam_risk": "voip_or_toll_free"},
            )
        )

    # -- optional: phone-to-social-account registration check ---------------
    if valid:
        _ignorant_social_check(str(number.national_number), str(number.country_code), report, float(timeout))
    else:
        report.errors.append("ignorant: skipped — phone number failed libphonenumber validity check")

    return report


# ─── Optional source implementation: Ignorant ───────────────────────────────


def _ignorant_social_check(phone: str, country_code: str, report: OsintReport, timeout: float) -> None:
    """Check which social platforms a phone number is registered to (optional).

    Runs Ignorant's Amazon / Instagram / Snapchat registration probes. This is
    an unauthenticated, HTTP-based signal: a positive result means the platform
    accepted the number during its public sign-in / lookup flow — treat as
    medium confidence and never as proof of identity.

    Degrades gracefully: a missing ``ignorant`` package, network failures, or
    per-site errors are recorded in ``report.errors`` and never crash the
    lookup.
    """
    if httpx is None:  # pragma: no cover
        report.errors.append("ignorant: httpx is not installed — phone-to-social check skipped")
        return
    if not _IGNORANT_CHECKS:
        report.errors.append(
            "ignorant: not installed — phone-to-social-account check skipped (pip install ignorant==1.2)"
        )
        return
    if timeout <= 0:
        report.errors.append("ignorant: timeout must be positive — check skipped")
        return

    per_site = max(5.0, min(float(timeout), 10.0))
    logger.debug(
        "ignorant phone-to-social check for +%s %s (per-site timeout %.1fs)",
        country_code,
        phone,
        per_site,
    )
    results: list[dict[str, Any]] = []
    site_errors: list[str] = []

    async def _probe() -> None:
        async with httpx.AsyncClient(timeout=per_site, follow_redirects=True) as client:
            for name, _domain, checker in _IGNORANT_CHECKS:
                try:
                    await checker(phone, country_code, client, results)
                except Exception as exc:  # third-party tool; never crash the lookup
                    site_errors.append(f"ignorant/{name}: {exc}")

    try:
        asyncio.run(_probe())
    except RuntimeError as exc:
        report.errors.append(f"ignorant: cannot run inside an active event loop: {exc}")
        return
    except Exception as exc:  # pragma: no cover — third-party tool; never crash the lookup
        report.errors.append(f"ignorant: unexpected failure: {exc}")
        return

    for error in site_errors:
        report.errors.append(error)

    rate_limited = [r for r in results if bool(r.get("rateLimit"))]
    found = [r for r in results if bool(r.get("exists"))]

    for r in rate_limited:
        report.errors.append(f"ignorant/{r.get('name', '?')}: rate limited — platform rejected the probe")

    for r in found:
        name = str(r.get("name") or "unknown")
        domain = str(r.get("domain") or "unknown")
        report.findings.append(
            OsintFinding(
                source="ignorant",
                category="account",
                summary=f"Phone number appears registered on {name} ({domain})",
                confidence="medium",
                data={
                    "platform": name,
                    "domain": domain,
                    "method": r.get("method"),
                    "rate_limited": bool(r.get("rateLimit")),
                },
            )
        )

    if results:
        domains = ", ".join(sorted({str(r.get("domain") or "?") for r in results}))
        report.findings.append(
            OsintFinding(
                source="ignorant",
                category="account",
                summary=(
                    f"Ignorant checked {len(results)} platform(s) ({domains}); "
                    f"number appears registered on {len(found)}"
                ),
                confidence="low",
                data={
                    "checked": [str(r.get("name") or "?") for r in results],
                    "registered": len(found),
                },
            )
        )
