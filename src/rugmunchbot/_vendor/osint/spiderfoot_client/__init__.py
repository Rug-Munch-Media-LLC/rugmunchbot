# VENDORED 2026-08-24 from rmi-backend/app/osint/spiderfoot_client/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""SpiderFoot OSINT automation engine client.

Runs a scan against a self-hosted SpiderFoot instance (``infra/spiderfoot/``)
and returns a unified :class:`app.osint.OsintReport`.

SpiderFoot is the general OSINT backbone of the section: given a domain,
email, username, wallet or IP it fans out across 100+ modules (DNS/WHOIS,
certificate-transparency logs, breach feeds, social/profile enumeration,
blockchain, …) and we map its per-event results onto our report model.

Package layout (mirrors :mod:`app.osint.domain` / :mod:`app.osint.face`):

* :mod:`app.osint.spiderfoot_client._core` — public entry point
  (:func:`lookup`) + scan lifecycle orchestration.
* :mod:`app.osint.spiderfoot_client._api` — low-level web-UI API calls.
* :mod:`app.osint.spiderfoot_client._mapping` — event-type -> finding mapping.

Contract: ``lookup(target, target_type="domain", *, timeout=120) ->
OsintReport`` (see :mod:`app.osint`).
"""

from __future__ import annotations

from rugmunchbot._vendor.osint.spiderfoot_client._core import lookup

__all__ = ["lookup"]
