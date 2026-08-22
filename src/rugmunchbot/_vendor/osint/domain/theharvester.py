# VENDORED 2026-08-17 from rmi-backend/app/osint/domain/theharvester.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""theHarvester source — passive email / hostname harvesting via the CLI.

Optional source: the binary is resolved with :func:`shutil.which` and any
absence/failure is logged and skipped — it never raises and never touches
``report.errors``, so WHOIS/DNS/SSL still produce a complete report.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.osint.domain._common import (
    _THEHARVESTER_LIMIT,
    _THEHARVESTER_SOURCES,
    _finding,
)

if TYPE_CHECKING:
    from rugmunchbot._vendor.osint import OsintReport
logger = logging.getLogger(__name__)


def _theharvester_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Harvest emails + hostnames via the ``theHarvester`` CLI (passive search).

    Runs ``theHarvester -d <domain> -b <sources> -l <limit> -q -f <prefix>``,
    which writes a structured ``<prefix>.json`` (plus an XML copy) that is
    parsed preferentially; the plain-text stdout sections (``[*] Emails
    found: N`` / ``[*] Hosts found: N``) are parsed as a fallback for
    versions or runs where the file is not written.
    """
    bin_path = shutil.which("theHarvester")
    if not bin_path:
        logger.error("theharvester: binary not found (install with: pip install theHarvester) - skipping")
        return

    fd, json_path = tempfile.mkstemp(prefix="theharvester-", suffix=".json")
    os.close(fd)
    prefix = json_path[: -len(".json")]
    try:
        try:
            # Fixed argv list (no shell); domain is regex-validated upstream and
            # passed as a single element — S603 is a false positive.
            result = subprocess.run(  # noqa: S603
                [
                    bin_path,
                    "-d",
                    domain,
                    "-b",
                    _THEHARVESTER_SOURCES,
                    "-l",
                    str(_THEHARVESTER_LIMIT),
                    "-q",
                    "-f",
                    prefix,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("theharvester: timed out after %ss", timeout)
            return
        except Exception as exc:
            logger.error("theharvester: CLI run failed: %s", exc)
            return
        if result.returncode != 0:
            logger.warning("theharvester: exited with code %s; parsing partial output", result.returncode)
        emails, hosts = _parse_theharvester_output(domain, json_path, result.stdout or "")
    finally:
        for suffix in (".json", ".xml"):
            with contextlib.suppress(OSError):
                os.unlink(f"{prefix}{suffix}")
    _emit_theharvester(domain, report, emails, hosts)


def _parse_theharvester_output(domain: str, json_path: str, stdout: str) -> tuple[list[str], list[str]]:
    """Parse theHarvester output into ``(emails, hosts)`` lists.

    Prefers the structured ``<prefix>.json`` written by ``-f`` (robust across
    versions); falls back to the plain-text ``[*] Emails found: N`` /
    ``[*] Hosts found: N`` sections when the file is absent or unparsable.
    """
    data: Any = None
    try:
        with open(json_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict):
        emails = [str(item) for item in (data.get("emails") or []) if item]
        hosts = [str(item) for item in (data.get("hosts") or []) if item]
        if emails or hosts:
            return _dedupe(emails), _dedupe(hosts)
    emails = _theharvester_section_items(stdout, "Emails found")
    hosts = _theharvester_section_items(stdout, "Hosts found")
    return _dedupe(emails), _dedupe(hosts)


def _theharvester_section_items(text: str, section: str) -> list[str]:
    """Extract the bullet items under a ``[*] <section>: N`` header.

    Tolerates the dashed separator line and blank lines; stops at the next
    ``[*]`` header or end of output.
    """
    pattern = re.compile(
        rf"^\[\*\]\s+{re.escape(section)}\s*:\s*\d+\s*\n(?:-+\s*\n)?(?P<body>.*?)(?=^\[\*\]|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return []
    items: list[str] = []
    for line in match.group("body").splitlines():
        line = line.strip()
        if not line or set(line) <= {"-"}:
            continue
        items.append(line)
    return items


def _classify_theharvester_hosts(hosts: list[str], domain: str) -> tuple[list[str], list[str]]:
    """Split theHarvester host entries into ``(subdomains, other-hosts)``.

    Entries are ``host`` or ``host:ip`` (theHarvester appends a resolved IP
    when a source resolves names; IPv6 addresses keep extra colons, so only
    the first segment matters). Names under ``domain`` count as subdomains;
    the bare domain, wildcards, and unrelated names count as hosts.
    """
    subdomains: set[str] = set()
    others: set[str] = set()
    suffix = f".{domain}"
    for entry in hosts:
        host = entry.split(":", 1)[0].strip().lower().rstrip(".")
        if not host:
            continue
        if host.startswith("*."):
            host = host[2:]
        if host == domain or host.endswith(suffix):
            subdomains.add(host)
        else:
            others.add(host)
    return sorted(subdomains), sorted(others)


def _dedupe(items: list[str]) -> list[str]:
    """Lowercase, dedupe, and sort a list of strings."""
    return sorted({item.strip().lower() for item in items if item.strip()})


def _emit_theharvester(domain: str, report: OsintReport, emails: list[str], hosts: list[str]) -> None:
    """Turn parsed theHarvester results into findings."""
    emails = sorted({email for email in emails if re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email)})
    subdomains, other_hosts = _classify_theharvester_hosts(hosts, domain)

    if emails:
        _finding(
            report,
            source="theharvester",
            category="email",
            summary=f"{len(emails)} email address(es) found by theHarvester",
            confidence="medium",
            emails=emails,
        )
    if subdomains:
        _finding(
            report,
            source="theharvester",
            category="subdomain",
            summary=f"{len(subdomains)} subdomain(s) found by theHarvester",
            confidence="medium",
            subdomains=subdomains,
        )
    if other_hosts:
        _finding(
            report,
            source="theharvester",
            category="host",
            summary=f"{len(other_hosts)} related host(s) found by theHarvester",
            confidence="medium",
            hosts=other_hosts,
        )
    if not (emails or subdomains or other_hosts):
        _finding(
            report,
            source="theharvester",
            category="recon",
            summary="theHarvester found no emails, subdomains, or hosts",
            confidence="medium",
        )
