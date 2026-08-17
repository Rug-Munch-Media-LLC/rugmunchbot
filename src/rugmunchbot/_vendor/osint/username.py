# VENDORED 2026-08-17 from rmi-backend/app/osint/username.py (Phase 2b — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Username OSINT — social / profile enumeration (Sherlock, maigret fallback).

Enumerates social profiles for a handle across platforms and returns a
unified :class:`app.osint.OsintReport` with ``target_type="username"``.

Two engines, in priority order:

* **Sherlock** (primary) — the ``sherlock-project`` CLI
  (https://github.com/sherlock-project/sherlock). Checks a bundled database
  of 400+ platforms (v0.16 ships 414 sites). Invoked via subprocess only and
  **never imported**, so this module imports cleanly even when the CLI is not
  installed.
* **maigret** (fallback) — the pre-existing ``app.osint_tools.lookup_username``
  wrapper, which runs the ``maigret`` CLI over its top-N Alexa-ranked sites.

Sherlock is bounded to a curated default site list (see :data:`DEFAULT_SITES`)
for interactive speed; pass ``sites=[]`` for a full sweep (needs a generous
``timeout``) or an explicit list to check only those platforms.

Error handling — import-safe, per-engine degradation:
  * Sherlock missing or failing (timeout, no report, parse error) -> a line in
    ``report.errors``, then the lookup falls back to maigret.
  * Maigret missing or failing -> a line in ``report.errors``; the lookup
    never raises for a single-engine failure.
  * Both engines missing -> :class:`OsintNotAvailable` with install
    instructions for both.
  * Garbage input (empty, email-looking, or containing whitespace / path
    separators) -> ``ValueError`` before any subprocess is spawned.

NOTE ON THE SHERLOCK CLI: sherlock >= 0.16 changed ``--json`` from an output
flag to a site-data *input* flag (``--json JSON_FILE``). This module therefore
parses Sherlock's machine-readable ``--csv`` report instead of ``--json``
stdout, and passes ``--timeout`` as the per-request HTTP timeout.

Usage::

    from rugmunchbot._vendor.osint.username import lookup_username

    report = lookup_username("soxoj", timeout=90)
    for finding in report.findings:
        print(finding.source, finding.summary, finding.data["url"])
    print(report.errors)

References
----------
- sherlock: https://github.com/sherlock-project/sherlock (``pip install sherlock-project``)
- maigret:  https://github.com/soxoj/maigret          (``pip install maigret``)
"""

from __future__ import annotations

import csv
import glob
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from rugmunchbot._vendor.osint import OsintError, OsintFinding, OsintNotAvailable, OsintReport
from rugmunchbot._vendor.osint_tools import (
    OsintError as MaigretError,
    OsintNotAvailable as MaigretNotAvailable,
    OsintResult as MaigretResult,
    lookup_username as maigret_lookup,
)

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_SITES", "lookup_username"]

# ─── Tunables ────────────────────────────────────────────────────────

INSTALL_SHERLOCK = "pip install sherlock-project"
INSTALL_MAIGRET = "pip install maigret"

#: Upper bound (seconds) for a single HTTP request inside the sherlock CLI.
#: Kept well below the module ``timeout`` so one slow site cannot consume the
#: whole budget (mirrors ``app.osint_tools.MAX_REQUEST_TIMEOUT``).
SHERLOCK_REQUEST_TIMEOUT = 15

#: Default ``top_sites`` for the maigret fallback (matches
#: ``app.osint_tools.DEFAULT_TOP_SITES``).
DEFAULT_MAIGRET_TOP_SITES = 30

# Usernames may be unicode but must never contain whitespace or path
# separators (sherlock uses the value in the CSV report filename).
_USERNAME_RE = re.compile(r"^[^\s/\\]{1,128}$")

#: Curated high-value platforms checked by default. Names must match the
#: ``name`` keys in sherlock's bundled ``data.json`` exactly (case-insensitive
#: match upstream). Verified against sherlock-project 0.16.0.
DEFAULT_SITES: tuple[str, ...] = (
    "GitHub",
    "GitLab",
    "Reddit",
    "Twitter",
    "Instagram",
    "LinkedIn",
    "Telegram",
    "Discord",
    "YouTube",
    "Bluesky",
    "threads",
    "Snapchat",
    "Mastodon.social",
    "VK",
    "Steam Community (User)",
    "Spotify",
    "SoundCloud",
    "Medium",
    "DEV Community",
    "HackerNews",
    "Keybase",
    "BitBucket",
    "Codeberg",
    "Patreon",
    "ProductHunt",
    "Freelancer",
    "Vimeo",
    "DailyMotion",
    "tumblr",
    "CashApp",
    "Venmo",
    "Hugging Face",
    "TradingView",
    "VirusTotal",
    "Imgur",
    "Pastebin",
    "last.fm",
    "About.me",
    "Docker Hub",
    "LeetCode",
)


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


def _run(argv: list[str], *, cwd: str, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run the sherlock CLI, translating subprocess failures into OSINT errors.

    The command vector is built from a resolved binary path plus fixed flags
    and a validated single user-supplied value; ``shell=False`` and a list
    argv mean no shell is involved, so S603 is a false positive here.
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
        raise OsintError(f"sherlock lookup timed out after {timeout}s") from exc


# ─── Sherlock engine ─────────────────────────────────────────────────


def _sherlock_rows(query: str, *, sites: Sequence[str] | None, timeout: int) -> list[dict[str, str]]:
    """Run the sherlock CLI for ``query`` and return its CSV rows.

    Args:
        query: Validated username to check.
        sites: Site names to check; ``None``/empty means the caller already
            resolved the default, empty list = full sweep (no ``--site``).
        timeout: Total wall-clock budget for the whole subprocess.

    Returns:
        One dict per checked site (columns: ``username``, ``name``,
        ``url_main``, ``url_user``, ``exists``, ``http_status``,
        ``response_time_s``).

    Raises:
        OsintNotAvailable: If the ``sherlock`` CLI is not installed.
        OsintError: If the CLI times out, exits without a report, or produces
            no CSV file.
    """
    sherlock_bin = _require_binary("sherlock", INSTALL_SHERLOCK)
    per_request = max(1, min(timeout, SHERLOCK_REQUEST_TIMEOUT))

    with tempfile.TemporaryDirectory(prefix="rmi_sherlock_") as tmp:
        argv = [sherlock_bin, query, "--csv", "--no-color", "--no-txt", "--timeout", str(per_request)]
        for site in sites or ():
            argv.extend(["--site", site])
        proc = _run(argv, cwd=tmp, timeout=timeout)

        csv_files = sorted(glob.glob(os.path.join(tmp, "*.csv")))
        if not csv_files:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise OsintError(f"sherlock produced no CSV report (rc={proc.returncode}): {detail[-500:]}")
        with open(csv_files[0], newline="", encoding="utf-8") as fh:
            return [dict(row) for row in csv.DictReader(fh)]


def _add_sherlock_findings(rows: list[dict[str, str]], query: str, report: OsintReport) -> int:
    """Map claimed sherlock CSV rows to report findings; returns the count."""
    found = 0
    for row in rows:
        site = str(row.get("name") or "").strip()
        if not site or str(row.get("exists") or "").strip().lower() != "claimed":
            continue
        data: dict[str, str] = {"url": str(row.get("url_user") or ""), "site": site}
        url_main = str(row.get("url_main") or "")
        if url_main:
            data["url_main"] = url_main
        http_status = str(row.get("http_status") or "").strip()
        if http_status and http_status != "?":
            data["http_status"] = http_status
        response_time = str(row.get("response_time_s") or "").strip()
        if response_time and response_time != "?":
            data["response_time_s"] = response_time
        report.findings.append(
            OsintFinding(
                source="sherlock",
                category="account",
                summary=f"{query} on {site}",
                confidence="high",
                data=data,
            )
        )
        found += 1
    return found


# ─── Maigret fallback ────────────────────────────────────────────────


def _add_maigret_findings(result: MaigretResult, query: str, report: OsintReport) -> int:
    """Normalize an ``app.osint_tools.OsintResult`` into report findings."""
    found = 0
    for site in result.sites:
        if not site.exists:
            continue
        data: dict[str, str] = {"url": site.url_user, "site": site.site}
        if site.url_main:
            data["url_main"] = site.url_main
        report.findings.append(
            OsintFinding(
                source="maigret",
                category="account",
                summary=f"{query} on {site.site}",
                confidence="high",
                data=data,
            )
        )
        found += 1
    return found


# ─── Public API ──────────────────────────────────────────────────────


def lookup_username(
    username: str,
    *,
    timeout: int = 120,
    sites: Sequence[str] | None = None,
    top_sites: int = DEFAULT_MAIGRET_TOP_SITES,
) -> OsintReport:
    """Check a username across social platforms (Sherlock, maigret fallback).

    Args:
        username: Handle to search for (1-128 chars, no whitespace or path
            separators, and not an email address — use ``app.osint.email``).
        timeout: Total wall-clock budget in seconds for the whole lookup.
            Also caps sherlock's per-request timeout and the maigret fallback
            budget.
        sites: Sherlock site names to check. ``None`` (default) uses the
            curated :data:`DEFAULT_SITES`; an explicit list checks only those
            platforms; ``[]`` runs a full sweep of sherlock's entire site
            database (requires a generous ``timeout``).
        top_sites: How many top (Alexa-ranked) sites the maigret fallback
            checks (passed through to ``app.osint_tools.lookup_username``).

    Returns:
        An :class:`OsintReport` whose findings list the platforms where the
        account exists, sourced from ``sherlock`` (primary) or ``maigret``
        (fallback). Engine failures are recorded in ``report.errors`` and
        never crash the lookup.

    Raises:
        ValueError: For empty / malformed usernames or bad ``top_sites`` /
            ``timeout``.
        OsintNotAvailable: If neither the ``sherlock`` nor the ``maigret`` CLI
            is installed.
    """
    query = username.strip()
    if not query:
        raise ValueError("username must be a non-empty string")
    if "@" in query:
        raise ValueError(f"{query!r} looks like an email address; use app.osint.email.lookup_email() instead")
    if not _USERNAME_RE.match(query):
        raise ValueError(f"invalid username {query!r}: must be 1-128 chars with no whitespace or path separators")
    if top_sites < 1:
        raise ValueError("top_sites must be >= 1")
    if timeout < 1:
        raise ValueError("timeout must be >= 1")

    report = OsintReport(target=query, target_type="username")

    # -- primary: sherlock --------------------------------------------------
    sherlock_missing = False
    rows: list[dict[str, str]] = []
    try:
        rows = _sherlock_rows(query, sites=sites if sites is not None else DEFAULT_SITES, timeout=timeout)
    except OsintNotAvailable as exc:
        report.errors.append(str(exc))
        sherlock_missing = True
    except OsintError as exc:
        report.errors.append(f"sherlock: {exc}")

    if rows:
        found = _add_sherlock_findings(rows, query, report)
        logger.info("sherlock_lookup_complete username=%s checked=%d found=%d", query, len(rows), found)
        return report

    # -- fallback: maigret --------------------------------------------------
    try:
        result = maigret_lookup(query, timeout=timeout, top_sites=top_sites)
    except MaigretNotAvailable as exc:
        report.errors.append(str(exc))
        if sherlock_missing:
            raise OsintNotAvailable(
                "username OSINT requires an installed engine: "
                f"Sherlock ({INSTALL_SHERLOCK}) or maigret ({INSTALL_MAIGRET})"
            ) from exc
        return report
    except MaigretError as exc:
        report.errors.append(f"maigret: {exc}")
        return report

    found = _add_maigret_findings(result, query, report)
    logger.info("maigret_lookup_fallback username=%s found=%d", query, found)
    return report
