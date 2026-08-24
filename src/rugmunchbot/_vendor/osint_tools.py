# VENDORED 2026-08-24 from rmi-backend/app/osint_tools.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""OSINT username / email profiling wrapper.

Wraps the ``maigret`` (username -> social accounts) and ``holehe`` (email ->
registered services) command-line tools as a bounded, stdlib-only
investigation utility for attributing scam / rug operators from a single
handle or email address.

The tools are executed via subprocess and **never imported**, so this module
has zero third-party runtime dependencies and imports cleanly even when the
CLIs are not installed -- in that case :class:`OsintNotAvailable` is raised
with install instructions.

Usage::

    from rugmunchbot._vendor.osint_tools import lookup_username, lookup_email

    result = lookup_username("cryptorugmunch", timeout=30, top_sites=30)
    for site in result.sites:
        print(site.site, site.url_user)  # "GitHub", "https://github.com/..."

    result = lookup_email("admin@example.com")
    print(result.found, result.sites)

References
----------
- maigret: https://github.com/soxoj/maigret  (``pip install maigret``)
- holehe:  https://github.com/megadose/holehe (``pip install holehe``)
"""

from __future__ import annotations

import csv
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ─── Tunables ────────────────────────────────────────────────────────

#: Number of top (Alexa-ranked) sites maigret checks by default. The maigret
#: default is 500 -- far too slow for interactive investigation. 30 covers the
#: major platforms in a reasonable time; raise it for exhaustive sweeps.
DEFAULT_TOP_SITES = 30

#: Upper bound (seconds) for a single HTTP request inside the wrapped CLIs.
#: Kept well below the module ``timeout`` so one slow site cannot consume the
#: whole budget.
MAX_REQUEST_TIMEOUT = 10

INSTALL_MAIGRET = "pip install maigret"
INSTALL_HOLEHE = "pip install holehe"

# Usernames may be unicode but must never contain whitespace or path
# separators (maigret uses the value in a report filename).
_USERNAME_RE = re.compile(r"^[^\s/\\]{1,128}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ─── Domain model ────────────────────────────────────────────────────


@dataclass(frozen=True)
class OsintSite:
    """A platform that was checked for the query.

    Attributes:
        site: Platform name (e.g. "GitHub").
        url_main: The platform's main URL (e.g. "https://github.com").
        url_user: The resolved profile URL for the query, if known.
        exists: Whether an account for the query exists on the platform.
    """

    site: str
    url_main: str
    url_user: str
    exists: bool


@dataclass(frozen=True)
class OsintResult:
    """Outcome of an OSINT lookup for a username or email.

    Attributes:
        username: The query string (username or email address).
        sites: Platforms where the account was found.
        raw: Unmodified tool output (maigret JSON object, or holehe CSV
            rows plus bookkeeping).
        tool: Engine that produced the result ("maigret" | "holehe").
    """

    username: str
    sites: list[OsintSite]
    raw: dict[str, Any]
    tool: str

    @property
    def found(self) -> bool:
        """True when at least one account exists for the query."""
        return bool(self.sites)


class OsintError(Exception):
    """Operational failure while running an OSINT lookup."""


class OsintNotAvailable(OsintError):
    """Required CLI tool is missing (see message for install instructions)."""


# ─── Internal helpers ────────────────────────────────────────────────


def _resolve_binary(name: str) -> str | None:
    """Locate a CLI binary, preferring the current virtualenv's bin dir.

    ``shutil.which`` relies on PATH, which does not include the venv ``bin``
    directory when the interpreter is invoked without activation (e.g.
    ``.venv/bin/python``). Checking ``sys.prefix/bin`` first makes the wrapper
    work in that common case.
    """
    venv_bin = os.path.join(sys.prefix, "bin", name)
    if os.path.isfile(venv_bin) and os.access(venv_bin, os.X_OK):
        return venv_bin
    return shutil.which(name)


def _require_binary(name: str, install_hint: str) -> str:
    """Return the resolved CLI path or raise :class:`OsintNotAvailable`."""
    path = _resolve_binary(name)
    if path is None:
        raise OsintNotAvailable(f"'{name}' CLI not found. Install it with: {install_hint}")
    return path


def _request_timeout(timeout: int) -> int:
    """Per-request timeout for the wrapped CLI, capped below the total budget."""
    return max(1, min(timeout, MAX_REQUEST_TIMEOUT))


def _run(argv: list[str], *, cwd: str | None, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run a CLI, translating subprocess failures into :class:`OsintError`.

    The command vector is built by the caller from a resolved binary path plus
    fixed flags and a validated single user-supplied value; ``shell=False`` and
    a list argv mean no shell is involved, so S603 is a false positive here.
    """
    try:
        # shell=False + list argv = no shell involved; command vector is a
        # resolved binary plus fixed flags and validated input (false positive)
        return subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        raise OsintNotAvailable(f"CLI binary disappeared before launch: {argv[0] if argv else '?'}") from exc
    except subprocess.TimeoutExpired as exc:
        raise OsintError(f"OSINT lookup timed out after {timeout}s") from exc


# ─── Username lookup via maigret ─────────────────────────────────────


def lookup_username(username: str, *, timeout: int = 30, top_sites: int = DEFAULT_TOP_SITES) -> OsintResult:
    """Check a username across social platforms using the maigret CLI.

    Args:
        username: Handle to search for (1-128 chars, no whitespace or path
            separators, and not an email address -- use :func:`lookup_email`).
        timeout: Total wall-clock budget in seconds for the whole lookup
            (also caps maigret's per-request timeout).
        top_sites: How many top (Alexa-ranked) sites to check. The maigret
            default of 500 is slow; this wrapper defaults to 30.

    Returns:
        An :class:`OsintResult` whose ``sites`` lists the platforms where the
        account exists (the maigret ``simple`` JSON report only contains
        claimed accounts).

    Raises:
        ValueError: For empty / malformed usernames or bad ``top_sites``.
        OsintNotAvailable: If the ``maigret`` CLI is not installed.
        OsintError: If the CLI times out or fails to produce a report.
    """
    query = username.strip()
    if not query:
        raise ValueError("username must be a non-empty string")
    if "@" in query:
        raise ValueError(f"{query!r} looks like an email address; use lookup_email() instead")
    if not _USERNAME_RE.match(query):
        raise ValueError(f"invalid username {query!r}: must be 1-128 chars with no whitespace or path separators")
    if top_sites < 1:
        raise ValueError("top_sites must be >= 1")

    maigret_bin = _require_binary("maigret", INSTALL_MAIGRET)

    with tempfile.TemporaryDirectory(prefix="rmi_osint_") as tmp:
        argv = [
            maigret_bin,
            query,
            "-J",
            "simple",  # single JSON object keyed by site name, claimed only
            "--top-sites",
            str(top_sites),
            "--timeout",
            str(_request_timeout(timeout)),
            "--folderoutput",
            tmp,
            "--no-recursion",  # do not chase secondary IDs (bounded runtime)
            "--no-extracting",  # do not parse pages for extra data (bounded)
            "--no-color",
            "--no-progressbar",
            "--no-autoupdate",  # deterministic startup, no network check
        ]
        proc = _run(argv, cwd=None, timeout=timeout)
        report_files = sorted(glob.glob(os.path.join(tmp, "*.json")))
        if not report_files:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise OsintError(f"maigret produced no JSON report (rc={proc.returncode}): {detail[-500:]}")
        with open(report_files[0], encoding="utf-8") as fh:
            loaded = json.load(fh)

    if not isinstance(loaded, dict):
        raise OsintError("maigret report is not a JSON object")
    raw: dict[str, Any] = loaded
    sites = _parse_maigret(raw)
    logger.info("maigret_lookup_complete username=%s found=%d", query, len(sites))
    return OsintResult(username=query, sites=sites, raw=raw, tool="maigret")


def _parse_maigret(raw: dict[str, Any]) -> list[OsintSite]:
    """Convert a maigret ``simple`` JSON report into :class:`OsintSite`s."""
    sites: list[OsintSite] = []
    for site_name, entry in raw.items():
        if not isinstance(entry, dict):
            continue

        site_info = entry.get("site")
        url_main = ""
        if isinstance(site_info, dict):
            url_main = str(site_info.get("urlMain") or "")
        if not url_main:
            url_main = str(entry.get("url_main") or "")

        # Only claimed accounts appear in the report; read the nested status
        # defensively and default to exists=True when it is absent.
        status = entry.get("status")
        status_value = status.get("status") if isinstance(status, dict) else None
        exists = status_value is None or str(status_value).lower() == "claimed"

        sites.append(
            OsintSite(
                site=str(site_name),
                url_main=url_main,
                url_user=str(entry.get("url_user") or ""),
                exists=exists,
            )
        )
    return sites


# ─── Email lookup via holehe ─────────────────────────────────────────


def lookup_email(email: str, *, timeout: int = 30) -> OsintResult:
    """Check which services an email address is registered on (holehe CLI).

    Args:
        email: Email address to check.
        timeout: Total wall-clock budget in seconds for the whole lookup.

    Returns:
        An :class:`OsintResult` whose ``sites`` lists the services where the
        email exists (rate-limited checks are skipped as inconclusive).

    Raises:
        ValueError: For malformed email addresses.
        OsintNotAvailable: If the ``holehe`` CLI is not installed.
        OsintError: If the CLI times out or fails to produce a report.
    """
    query = email.strip()
    if not _EMAIL_RE.match(query):
        raise ValueError(f"invalid email address: {query!r}")

    holehe_bin = _require_binary("holehe", INSTALL_HOLEHE)

    with tempfile.TemporaryDirectory(prefix="rmi_osint_") as tmp:
        argv = [
            holehe_bin,
            query,
            "--no-color",
            "--no-clear",
            "-NP",  # skip password-recovery probes (slower, noisy)
            "-C",  # CSV export for machine parsing
            "-T",
            str(_request_timeout(timeout)),
        ]
        proc = _run(argv, cwd=tmp, timeout=timeout)
        csv_files = sorted(glob.glob(os.path.join(tmp, "holehe_*.csv")))
        if not csv_files:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise OsintError(f"holehe produced no CSV report (rc={proc.returncode}): {detail[-500:]}")
        rows = _read_csv(csv_files[0])

    sites = _parse_holehe(rows)
    raw: dict[str, Any] = {"email": query, "rows": rows, "checked": len(rows)}
    logger.info(
        "holehe_lookup_complete email=%s found=%d checked=%d",
        query,
        len(sites),
        len(rows),
    )
    return OsintResult(username=query, sites=sites, raw=raw, tool="holehe")


def _read_csv(path: str) -> list[dict[str, str]]:
    """Read a holehe CSV report into a list of rows."""
    with open(path, newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _parse_holehe(rows: list[dict[str, str]]) -> list[OsintSite]:
    """Convert holehe CSV rows into :class:`OsintSite`s (existing accounts only)."""
    sites: list[OsintSite] = []
    for row in rows:
        if not row.get("name"):
            continue
        # Rate-limited rows are inconclusive -- never claim existence on them.
        if str(row.get("rateLimit", "")).strip().lower() in {"true", "1", "yes"}:
            continue
        if str(row.get("exists", "")).strip().lower() not in {"true", "1", "yes"}:
            continue
        domain = row.get("domain", "")
        sites.append(
            OsintSite(
                site=row["name"],
                url_main=f"https://{domain}" if domain else "",
                url_user="",
                exists=True,
            )
        )
    return sites


__all__ = [
    "DEFAULT_TOP_SITES",
    "OsintError",
    "OsintNotAvailable",
    "OsintResult",
    "OsintSite",
    "lookup_email",
    "lookup_username",
]
