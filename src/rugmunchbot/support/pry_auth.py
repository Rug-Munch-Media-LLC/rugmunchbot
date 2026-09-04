"""Pry API authentication helpers — shared by every Pry scrape call site.

Pry is OUR app (self-hosted on the host loopback at ``127.0.0.1:8005``). Its
``/v1/scrape`` (and friends) endpoints are gated behind x402 payment — a
request without a credential returns ``402 Payment Required``. Because we
host Pry, we bypass the on-chain payment with the internal API key:

* ``PRY_API_KEY`` set  -> send ``Authorization: Bearer {PRY_API_KEY}``
  (the Pry middleware validates this Bearer credential from every source,
  loopback included — see ``pry.core.middleware.request_authorized``).
* ``PRY_X402_OFFLINE`` set -> additionally send ``X-X402-Offline: true``
  (a no-op on the server today, but harmless and future-proof; offline mode
  is really a server-side env toggle, the Bearer key is what unlocks it).

Both env vars are read once at import time so the headers are stable for the
process lifetime. ``pry_headers()`` returns the dict to merge into every
httpx call to Pry.
"""

from __future__ import annotations

import os

#: Pry internal API key (gopass ``pry/api_key`` / ``/srv/rmi-infra/.env.pry``).
#: Empty when unset — the caller then sends no Authorization header.
PRY_API_KEY: str = os.getenv("PRY_API_KEY", "").strip()
#: x402 offline-mode flag (server-side bypass; the header is belt-and-braces).
PRY_X402_OFFLINE: bool = os.getenv("PRY_X402_OFFLINE", "false").lower() in (
    "1",
    "true",
    "yes",
)


def pry_headers() -> dict[str, str]:
    """Return the auth headers to attach to every Pry scrape request.

    Always includes ``Content-Type: application/json``. Adds
    ``Authorization: Bearer {PRY_API_KEY}`` when the key is configured and
    ``X-X402-Offline: true`` when offline mode is enabled.
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if PRY_API_KEY:
        headers["Authorization"] = f"Bearer {PRY_API_KEY}"
    if PRY_X402_OFFLINE:
        headers["X-X402-Offline"] = "true"
    return headers


__all__ = ["PRY_API_KEY", "PRY_X402_OFFLINE", "pry_headers"]
