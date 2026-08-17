# VENDORED 2026-08-17 from rmi-backend/app/osint/spiderfoot_client/_mapping.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Event-type mapping for the SpiderFoot client.

Maps SpiderFoot's ``scaneventresults`` rows onto the OSINT report model:
raw event types -> report categories, module names -> readable sources,
confidence/false-positive values -> high/medium/low, and the row -> finding
conversion. Purely functional — no I/O, no environment state — so it can be
unit-tested in isolation.
"""

from __future__ import annotations

import html
import re
from typing import Any, Final

from rugmunchbot._vendor.osint import OsintFinding

#: Seed/ROOT events (the target itself) are noise — skip them.
_SKIP_EVENT_TYPES: frozenset[str] = frozenset({"ROOT"})

#: Event types SpiderFoot never produces findings for even if seen.
_NOISE_EVENT_TYPES: frozenset[str] = frozenset({"WEBSERVER_HTTP_HEADERS"})

#: Mapping of interesting SpiderFoot event types to our report categories.
#: Falls back to a snake_case version of the SpiderFoot type.
_EVENT_CATEGORY: dict[str, str] = {
    "DOMAIN_NAME": "domain",
    "INTERNET_NAME": "domain",
    "IP_ADDRESS": "ip_address",
    "IPV6_ADDRESS": "ip_address",
    "NETBLOCK_OWNER": "netblock",
    "EMAILADDR": "email",
    "USERNAME": "username",
    "PHONE_NUMBER": "phone",
    "BITCOIN_ADDRESS": "bitcoin",
    "ETHEREUM_ADDRESS": "ethereum",
    "HUMAN_NAME": "person",
    "ORG_NAME": "organization",
    "AFFILIATE_INTERNET_NAME": "affiliate",
    "SUBDOMAIN": "subdomain",
    "WEBSERVER_TECHNOLOGY": "technology",
    "SSL_CERTIFICATE_ISSUED": "certificate",
    "SSL_CERTIFICATE_ISSUER": "certificate",
    "DNS_SERVER": "dns",
    "MAIL_SERVER": "dns",
    "NAMESERVER": "dns",
    "CO_HOSTED_SITE": "co_hosted",
    "PHYSICAL_ADDRESS": "address",
    "GEOINFO": "geolocation",
    "SOCIAL_MEDIA": "social",
    "SOCIAL_PROFILE": "social",
    "ACCOUNT": "account",
    "BREACHED_ACCOUNT": "breach",
    "LEAKED_ACCOUNT": "breach",
    "SANCTIONED_ENTITY": "sanction",
    "BLACKLISTED_IP": "blacklist",
    "VULNERABILITY": "vulnerability",
    "REPUTATION": "reputation",
    "HISTORIC_WHOIS": "whois",
    "DOMAIN_REGISTRAR": "whois",
    "DOMAIN_WHOIS": "whois",
    "OPERATING_SYSTEM": "technology",
    "PROVIDER_TELCO": "provider",
    "PROVIDER_HOSTING": "provider",
    "PROVIDER_DNS": "provider",
    "RAW_DATA": "raw",
    "SEARCH_ENGINE_WEB_CONTENT": "web_content",
}

#: SpiderFoot module prefix we strip for readable ``source`` values.
_MODULE_PREFIX = "sfp_"

#: Upper bound on the summary/data string length we put in a finding.
_MAX_FIELD_LENGTH: Final = 5000


def _category_from_type(event_type: str) -> str:
    """Map a SpiderFoot event type to our report category."""
    mapped = _EVENT_CATEGORY.get(event_type.upper())
    if mapped:
        return mapped
    # Fallback: DOMAIN_NAME_EXTRA -> "domain_name_extra"
    words = re.findall(r"[A-Z0-9]+(?:[a-z0-9]+)?", event_type)
    return "_".join(words).lower() if words else event_type.lower()


def _source_from_module(module: str) -> str:
    """Map a SpiderFoot module name (sfp_whois) to a readable source."""
    name = module or "spiderfoot"
    if name.startswith(_MODULE_PREFIX):
        name = name[len(_MODULE_PREFIX) :]
    return name or "spiderfoot"


def _confidence_from_values(confidence: Any, false_positive: Any) -> str:
    """Derive high/medium/low from SpiderFoot confidence % and the fp flag."""
    if str(false_positive).strip() in ("1", "True", "true"):
        return "low"
    try:
        value = float(confidence)  # 0-100
    except (TypeError, ValueError):
        value = 50.0
    if value >= 70:
        return "high"
    if value >= 40:
        return "medium"
    return "low"


def _clean_text(value: Any) -> str:
    """Normalize a SpiderFoot data field to a safe, bounded string."""
    text = str(value or "").strip()
    text = html.unescape(text)  # scaneventresults HTML-escapes data/source
    text = re.sub(r"<SFURL>|</SFURL>", "", text)
    text = re.sub(r"\s+", " ", text)
    if len(text) > _MAX_FIELD_LENGTH:
        text = text[:_MAX_FIELD_LENGTH] + "…"
    return text


def _row_to_finding(row: list[Any]) -> OsintFinding | None:
    """Map one SpiderFoot ``scaneventresults`` row to an :class:`OsintFinding`."""
    # [lastseen, data, source_data, module, confidence, visibility, risk,
    #  hash, fp, parent_fp, event_type]
    if len(row) < 11:
        return None
    event_type = str(row[10] or "").upper()
    if event_type in _SKIP_EVENT_TYPES or event_type in _NOISE_EVENT_TYPES:
        return None

    module = str(row[3] or "")
    data_text = _clean_text(row[1])
    source_data = _clean_text(row[2])
    false_positive = str(row[8]).strip() in ("1", "True", "true")
    confidence = _confidence_from_values(row[4], row[8])
    risk = _clean_text(row[6])

    summary = data_text
    if not summary:
        summary = f"{_source_from_module(module)} reported {event_type.lower()}"

    details: dict[str, Any] = {
        "event_type": event_type,
        "module": module,
        "false_positive": false_positive,
    }
    if risk:
        details["risk"] = risk
    if source_data:
        details["source_data"] = source_data
    last_seen = _clean_text(row[0])
    if last_seen:
        details["last_seen"] = last_seen

    return OsintFinding(
        source=_source_from_module(module),
        category=_category_from_type(event_type),
        summary=summary,
        confidence=confidence,
        data=details,
    )


def _dedupe_findings(findings: list[OsintFinding]) -> list[OsintFinding]:
    """Remove exact duplicates (same source/category/summary) while keeping order."""
    seen: set[tuple[str, str, str]] = set()
    out: list[OsintFinding] = []
    for finding in findings:
        key = (finding.source, finding.category, finding.summary)
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out
