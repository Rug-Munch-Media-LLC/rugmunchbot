"""THE registry adapter — one place that knows every channel-registry schema.

Inputs, in priority order:

1. ``RMI_SHILL_REGISTRY_PATH`` (explicit): a ``.json`` or the coverage lane's
   ``tg_channels.txt``.
2. The ``telegram-coverage`` lane output (``tg_channels.txt``): one handle
   per line, ``#`` comments — ``rugbrain.ingest_sources.channel_registry``
   format.
3. The bot's current registry, ``data/channel_trust.json`` (handle/title/
   class/trust/monitored_only) via ``channels.channel_trust``.
4. The vendored backend registry JSON (``username``/``name``/``kind``/
   ``category``/``enabled``) when the file ships.

A schema change is handled HERE and nowhere else. Absence is never an error:
an empty registry means "no channels to monitor", and the lane degrades.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


_REPO = Path(__file__).resolve().parents[4]
_DEFAULT_TXT = (
    _REPO / "data" / "tg_channels.txt",
    Path("/var/lib/rugmunchbot/tg_channels.txt"),
)
_DEFAULT_TRUST = _REPO / "data" / "channel_trust.json"
_DEFAULT_VENDOR = _REPO / "src" / "rugmunchbot" / "_vendor" / "tg_channels.json"


@dataclass(frozen=True, slots=True)
class Channel:
    """Normalized registry row — every schema collapses into this."""

    handle: str
    title: str = ""
    klass: str = "unknown"
    trust: float = 1.0
    monitored_only: bool = False
    origin: str = ""

    @property
    def shill_prone(self) -> bool:
        """Channels whose whole business is pushing contracts."""
        return self.klass in ("token-shiller", "shill_promotions", "scam")


def _handle(raw: object) -> str:
    value = str(raw or "").strip()
    if "t.me/" in value:
        value = value.split("t.me/", 1)[1]
    return value.strip().strip("/").lstrip("@").lower()


def _from_txt(path: Path) -> list[Channel]:
    """Coverage-lane handle list: one per line, ``#`` comments ignored."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        logger.debug("best-effort fallback: %s", e)
        return []
    out: list[Channel] = []
    for line in lines:
        handle = _handle(line)
        if handle and not handle.startswith("#"):
            out.append(Channel(handle=handle, origin="coverage-lane"))
    return out


def _from_trust(path: Path) -> list[Channel]:
    """Bot registry (channel_trust.json) — the current rugbrain format."""
    try:
        from rugmunchbot.channels.channel_trust import load_channel_trust
    except Exception as e:  # pragma: no cover - import guard for island builds
        logger.debug("best-effort fallback: %s", e)
        return []
    registry = load_channel_trust(path)
    return [
        Channel(
            handle=entry.handle,
            title=entry.title,
            klass=entry.klass,
            trust=entry.trust,
            monitored_only=entry.monitored_only,
            origin="channel_trust",
        )
        for entry in registry.entries.values()
    ]


def _from_vendor_json(path: Path) -> list[Channel]:
    """Vendored backend registry: ``{"channels": [{username, kind, ...}]}``."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.debug("best-effort fallback: %s", e)
        return []
    rows = payload.get("channels") if isinstance(payload, dict) else payload
    out: list[Channel] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        handle = _handle(row.get("username") or row.get("handle"))
        if not handle:
            continue
        out.append(
            Channel(
                handle=handle,
                title=str(row.get("name") or row.get("title") or ""),
                klass=str(row.get("category") or row.get("kind") or "unknown"),
                trust=1.0 if row.get("reliable", True) else 0.3,
                monitored_only=str(row.get("kind") or "") in ("scammer_caller", "intel"),
                origin="vendor-json",
            )
        )
    return out


def _from_any_json(path: Path) -> list[Channel]:
    """A JSON file whose schema we must detect — trust vs vendor, one place."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.debug("best-effort fallback: %s", e)
        return []
    rows = payload.get("channels") if isinstance(payload, dict) else payload
    looks_like_trust = isinstance(rows, list) and any(
        isinstance(row, dict) and ("class" in row or "trust" in row) for row in rows
    )
    return _from_trust(path) if looks_like_trust else _from_vendor_json(path)


def load_registry(path: str | os.PathLike[str] | None = None) -> list[Channel]:
    """Load the monitored-channel registry; ``[]`` when nothing is available."""
    explicit = path or os.getenv("RMI_SHILL_REGISTRY_PATH")
    if explicit:
        target = Path(explicit)
        if target.is_dir():
            for name in ("tg_channels.txt", "channel_trust.json", "tg_channels.json"):
                candidate = target / name
                if candidate.is_file():
                    target = candidate
                    break
        if target.suffix == ".txt":
            return _from_txt(target)
        if target.is_file():
            return _from_any_json(target)
        return []

    for candidate in _DEFAULT_TXT:
        if candidate.is_file():
            return _from_txt(candidate)
    if _DEFAULT_TRUST.is_file():
        return _from_trust(_DEFAULT_TRUST)
    if _DEFAULT_VENDOR.is_file():
        return _from_vendor_json(_DEFAULT_VENDOR)
    return []
