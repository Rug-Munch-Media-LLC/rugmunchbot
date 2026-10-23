"""One JSON GET against the RMI backend (shared policy)."""

from __future__ import annotations

from typing import Any

from rugmunchbot.config import BACKEND_URL, backend_headers
from rugmunchbot.tools._client import http_get_json


async def backend_json(
    path: str, params: dict[str, Any] | None = None, timeout: float = 12.0
) -> dict[str, Any] | None:
    """GET ``path`` on the backend. ``None`` on any failure — never raises."""
    return await http_get_json(
        f"{BACKEND_URL}{path}", params=params, headers=backend_headers(), timeout=timeout
    )
