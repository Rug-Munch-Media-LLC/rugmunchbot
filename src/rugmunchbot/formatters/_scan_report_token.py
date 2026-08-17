"""Token identity helpers for the scan report (image + description).

Split out of ``formatters/_scan_report.py`` during the Batch 14
de-godding pass: best-effort fail-open extractors used by the Socials /
Description section of ``format_scan_report``."""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "_token_description",
    "_token_image_url",
]


def _token_image_url(r: dict[str, Any]) -> str:
    """Best-effort token image URL from a scan result (fail-open).

    Accepts any of the keys the scanner/backends may use for the token
    logo: ``image_uri`` (Pump.fun API), ``image_url`` (DexScreener /
    generic), ``logo_url`` (Birdeye / legacy). Returns the first
    http(s) value found, else "".
    """
    for key in ("image_uri", "image_url", "logo_url", "image"):
        val = r.get(key)
        if isinstance(val, str) and val.strip().startswith(("http://", "https://")):
            return val.strip()
    return ""


def _token_description(r: dict[str, Any]) -> str:
    """Best-effort token description from a scan result (fail-open).

    Prefers the top-level ``description`` key (populated by the scanner
    when available) and falls back to the SENTINEL metadata-fingerprint
    module's ``description_snippet`` (Pump.fun / DexScreener description,
    metadata_fingerprint.py).
    """
    desc = r.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    try:
        mf = (r.get("sentinel") or {}).get("free", {}).get("metadata_fingerprint")
        if isinstance(mf, dict):
            snippet = mf.get("description_snippet")
            if isinstance(snippet, str) and snippet.strip():
                return snippet.strip()
    except (AttributeError, TypeError) as e:
        logger.debug("token meta parse failed: %s", e)
    return ""
