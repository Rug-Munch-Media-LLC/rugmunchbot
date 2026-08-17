# VENDORED 2026-08-17 from rmi-backend/app/osint/domain/amass.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Amass source — passive DNS / subdomain mapping via the ``amass`` CLI.

Version-aware: amass v3 accepts ``enum -passive -d <domain> -json <file>``
(JSON-Lines output); amass v4 removed ``-json`` and writes graph-edge text
via ``enum -passive -nocolor -d <domain> -oA <prefix>`` instead. Both formats
are parsed into subdomain / address (IP/CIDR) / ASN findings.

Optional source: identical degradation semantics to theHarvester — a missing
binary or failed run is logged and skipped, never raises, and never touches
``report.errors``.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.osint.domain._common import _finding

if TYPE_CHECKING:
    from rugmunchbot._vendor.osint import OsintReport
logger = logging.getLogger(__name__)

#: Cached result of sniffing whether the ``amass`` binary supports ``-json``
#: (amass v3 does; v4 dropped the flag in favour of ``-oA``).
_AMASS_JSON_SUPPORTED: bool | None = None

#: amass v4 ``-oA`` text output lines are graph edges: ``a --> TYPE --> b``.
_AMASS_EDGE_RE = re.compile(r"^(?P<left>.+?)\s+-->\s+(?P<type>[A-Za-z0-9_-]+)\s+-->\s+(?P<right>.+)$")

#: ANSI escape sequences amass may emit for colorized output.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _amass_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Passive DNS/subdomain mapping via the ``amass`` CLI (OWASP Amass)."""
    bin_path = shutil.which("amass")
    if not bin_path:
        logger.error(
            "amass: binary not found (install with: go install github.com/owasp-amass/amass/v4/...@latest"
            " or apt install amass on Kali) - skipping"
        )
        return

    fd, out_path = tempfile.mkstemp(prefix="amass-", suffix=".json")
    os.close(fd)
    out_prefix = out_path[: -len(".json")]
    try:
        try:
            # Fixed argv list (no shell); domain is regex-validated upstream and
            # passed as a single element — S603 is a false positive.
            if _amass_supports_json(bin_path):
                argv = [bin_path, "enum", "-passive", "-d", domain, "-json", out_path]
            else:
                argv = [bin_path, "enum", "-passive", "-nocolor", "-d", domain, "-oA", out_prefix]
            result = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("amass: timed out after %ss", timeout)
            return
        except Exception as exc:
            logger.error("amass: CLI run failed: %s", exc)
            return
        if result.returncode != 0:
            logger.warning("amass: exited with code %s; parsing partial output", result.returncode)
        try:
            if _amass_supports_json(bin_path):
                entries = _read_amass_json(out_path)
            else:
                entries = _parse_amass_txt(_read_amass_txt(out_prefix + ".txt"), domain)
        except OSError as exc:
            logger.error("amass: could not read output: %s", exc)
            return
    finally:
        with contextlib.suppress(OSError):
            os.unlink(out_path)
        with contextlib.suppress(OSError):
            os.unlink(out_prefix + ".txt")
    _emit_amass(domain, report, entries)


def _amass_supports_json(bin_path: str) -> bool:
    """Return whether the ``amass`` binary still supports ``-json`` (v3 vs v4).

    Sniffed once per process from ``amass enum -h`` and cached; on any sniff
    failure we assume v4-style (``-oA`` is accepted by both v3 and v4).
    """
    global _AMASS_JSON_SUPPORTED
    if _AMASS_JSON_SUPPORTED is None:
        try:
            probe = subprocess.run(  # noqa: S603
                [bin_path, "enum", "-h"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                shell=False,
            )
            _AMASS_JSON_SUPPORTED = "-json" in (probe.stdout or "") or "-json" in (probe.stderr or "")
        except Exception as exc:
            logger.error("amass: could not probe version capabilities: %s", exc)
            _AMASS_JSON_SUPPORTED = False
    return _AMASS_JSON_SUPPORTED


def _read_amass_json(json_path: str) -> list[dict[str, Any]]:
    """Read amass ``-json`` output (one JSON object per line, invalid lines skipped)."""
    entries: list[dict[str, Any]] = []
    with open(json_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                entries.append(obj)
    return entries


def _read_amass_txt(txt_path: str) -> str:
    """Read amass ``-oA`` text output, stripping ANSI escape sequences."""
    with open(txt_path, encoding="utf-8", errors="replace") as handle:
        return _ANSI_RE.sub("", handle.read())


def _parse_amass_txt(text: str, domain: str) -> list[dict[str, Any]]:
    """Parse amass v4 ``-oA`` text output (graph-edge lines) into normalized entries.

    Each line is ``left --> TYPE --> right`` where either side may carry an
    ``(AssetType)`` suffix (``FQDN`` / ``IPAddress`` / ``Netblock`` / ``ASN``);
    bare lines are tolerated. Normalized entries reuse the v3 JSON-Lines keys
    so both paths share ``_emit_amass``.
    """
    entries: list[dict[str, Any]] = []
    suffix = f".{domain}"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _AMASS_EDGE_RE.match(line)
        segments = [match.group("left"), match.group("right")] if match else [line]
        for segment in segments:
            name, asset_type = _split_amass_asset(segment)
            if not name:
                continue
            if asset_type == "IPAddress" or _looks_like_ip(name):
                entries.append({"name": name, "addresses": [{"ip": name, "cidr": ""}]})
            elif asset_type == "Netblock" or _looks_like_cidr(name):
                entries.append({"addresses": [{"ip": "", "cidr": name}]})
            elif asset_type == "ASN" or (name.isdigit() and len(name) <= 10):
                entries.append({"asn": name})
            elif name == domain or name.endswith(suffix):
                entries.append({"name": name})
    return entries


def _split_amass_asset(segment: str) -> tuple[str, str]:
    """Split ``name (AssetType)`` into ``(name, AssetType)``; tolerate bare names."""
    segment = segment.strip()
    if " (" in segment and segment.endswith(")"):
        name, _, asset_type = segment.rpartition("(")
        return name.strip().lower().rstrip("."), asset_type.rstrip(")").strip()
    return segment.strip().lower().rstrip("."), ""


def _looks_like_ip(value: str) -> bool:
    """Heuristic: bare IPv4/IPv6 literal (no DNS lookup needed)."""
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _looks_like_cidr(value: str) -> bool:
    """Heuristic: bare CIDR block like ``93.184.216.0/24``."""
    return "/" in value and not value.endswith(".")


def _emit_amass(domain: str, report: OsintReport, entries: list[dict[str, Any]]) -> None:
    """Turn amass entries (JSON-Lines or normalized text) into findings."""
    subdomains: set[str] = set()
    addresses: set[tuple[str, str]] = set()  # (ip, cidr)
    asns: set[tuple[str, str]] = set()  # (asn, description)
    suffix = f".{domain}"

    for entry in entries:
        name = entry.get("name")
        if isinstance(name, str):
            name = name.strip().lower().rstrip(".")
            if name.startswith("*."):
                name = name[2:]
            if name == domain or name.endswith(suffix):
                subdomains.add(name)
        for addr in entry.get("addresses") or []:
            if isinstance(addr, dict):
                ip = addr.get("ip")
                cidr = addr.get("cidr")
                if isinstance(ip, str) and isinstance(cidr, str) and (ip.strip() or cidr.strip()):
                    addresses.add((ip.strip(), cidr.strip()))
        asn = entry.get("asn")
        desc = entry.get("desc")
        if asn is not None:
            asns.add((str(asn), str(desc) if isinstance(desc, str) and desc.strip() else ""))

    if subdomains:
        _finding(
            report,
            source="amass",
            category="subdomain",
            summary=f"{len(subdomains)} subdomain(s) discovered by Amass passive enumeration",
            confidence="medium",
            subdomains=sorted(subdomains),
        )
    if addresses:
        _finding(
            report,
            source="amass",
            category="address",
            summary=f"{len(addresses)} address(es) resolved by Amass",
            confidence="medium",
            addresses=[{"ip": ip, "cidr": cidr} for ip, cidr in sorted(addresses)],
        )
    if asns:
        _finding(
            report,
            source="amass",
            category="asn",
            summary=f"{len(asns)} ASN(s) associated with {domain} by Amass",
            confidence="medium",
            asns=[{"asn": asn, "desc": desc} for asn, desc in sorted(asns)],
        )
    if not (subdomains or addresses or asns):
        _finding(
            report,
            source="amass",
            category="recon",
            summary="Amass passive enumeration found no subdomains, addresses, or ASNs",
            confidence="medium",
        )
