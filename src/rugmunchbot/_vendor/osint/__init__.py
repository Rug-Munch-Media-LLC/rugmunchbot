# VENDORED 2026-08-17 from rmi-backend/app/osint/__init__.py (Phase 2b — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""OSINT (open-source intelligence) section — attribute and track down scammers.

Given a username, email, domain, wallet address, or phone number, return a
unified :class:`OsintReport` of findings from self-hosted best-in-class tooling.

Module contract (each module exposes ``lookup(target, *, timeout=60) -> OsintReport``):

    username    social/profile enumeration (maigret)            — app.osint_tools
    email       account + breach enumeration (holehe, HIBP, emailrep)
    domain      WHOIS / DNS / subdomains / SSL / web-archive / tech-fingerprint
    wallet      entity attribution, exchange labels, scam reports, sanctions
    phone       validation / carrier / region / timezone
    spiderfoot  general OSINT automation engine (self-hosted SpiderFoot)

Composes with: app.osint_tools (maigret/holehe), app.ocr_utils (PaddleOCR),
app.wallet_label_loader / app.entity_registry (crypto attribution), infra/archivebox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["OsintError", "OsintFinding", "OsintNotAvailable", "OsintReport"]


class OsintError(RuntimeError):
    """Base error for OSINT lookups."""


class OsintNotAvailable(OsintError):
    """Raised when the underlying tool/service for a module is not installed."""


@dataclass
class OsintFinding:
    """A single resolved fact about the target."""

    source: str  # e.g. "whois", "crt.sh", "holehe", "chainabuse"
    category: str  # e.g. "registrant", "subdomain", "account", "sanction"
    summary: str  # one-line human-readable summary
    confidence: str = "medium"  # high | medium | low
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "category": self.category,
            "summary": self.summary,
            "confidence": self.confidence,
            "data": self.data,
        }


@dataclass
class OsintReport:
    """Aggregated result for one target."""

    target: str
    target_type: str  # username | email | domain | wallet | phone
    findings: list[OsintFinding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def found(self) -> int:
        return len(self.findings)

    def add(
        self,
        source: str,
        category: str,
        summary: str,
        *,
        confidence: str = "medium",  # high | medium | low
        **data: Any,
    ) -> None:
        self.findings.append(
            OsintFinding(source=source, category=category, summary=summary, confidence=confidence, data=data)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "target_type": self.target_type,
            "findings": [f.to_dict() for f in self.findings],
            "errors": self.errors,
        }
