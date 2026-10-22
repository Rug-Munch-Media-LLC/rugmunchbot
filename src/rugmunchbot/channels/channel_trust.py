"""Channel trust registry — WHO may be treated as a news source.

Operator directive (2026-10-23): named channels are classified by hand from
their public ``t.me/s/<handle>`` preview and scored ``0..1``. The registry is
data (``data/channel_trust.json``); this module is the only loader, so the
news/content intake, the shill watch and any future report renderer read ONE
classification instead of each carrying its own blocklist.

Classes
-------
``token-shiller``   pumps a contract address then exits. NEVER a news source.
``macro-clickbait`` unsourced all-caps macro posts. Eligible, but down-weighted.
``aggregator``      republishes upstream feeds; trusted at its ``trust`` value.
``primary``         first-party source (exchange, security firm, explorer).
``unknown``         not in the registry: neutral (eligible, weight 1.0).

Usage
-----
    from rugmunchbot.channels.channel_trust import load_channel_trust

    trust = load_channel_trust()
    trust.news_eligible("darkycalls")   # False
    trust.weight("danny_crypton")       # 0.075 (down-weighted clickbait)
    trust.news_sources(["darkycalls", "@peckshield"])  # ["@peckshield"]

Fail-open on a missing registry file (warn + empty registry): every channel
then scores as ``unknown`` so intake keeps running. A missing file is a
deploy defect, not a reason to drop the news lane.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)

REGISTRY_VERSION: Final = 1
CLASSES: Final[frozenset[str]] = frozenset(
    {"token-shiller", "macro-clickbait", "aggregator", "primary", "unknown"}
)
DOWNWEIGHT: Final[dict[str, float]] = {
    "token-shiller": 0.0,
    "macro-clickbait": 0.5,
    "aggregator": 1.0,
    "primary": 1.0,
    "unknown": 1.0,
}
DEFAULT_PATH: Final[Path] = Path(__file__).resolve().parents[3] / "data" / "channel_trust.json"
ENV_PATH: Final[str] = "RMI_CHANNEL_TRUST_PATH"


def normalize(handle: str) -> str:
    """Registry key for a handle: ``@`` stripped, lowercased, URL reduced."""
    raw = str(handle or "").strip().lower().rstrip("/")
    if raw.startswith(("https://t.me/", "http://t.me/")):
        raw = raw.split("t.me/", 1)[1]
    return raw.lstrip("@")


@dataclass(frozen=True)
class ChannelTrust:
    """One classified channel; see the registry schema for field meaning."""

    handle: str
    title: str = ""
    subscribers: int | None = None
    klass: str = "unknown"
    trust: float = 1.0
    monitored_only: bool = False
    evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def news_eligible(self) -> bool:
        """False for channels that push contract addresses and exit."""
        return self.klass != "token-shiller"

    @property
    def weight(self) -> float:
        """Reliability weight applied when scoring/ranking ingested items."""
        return max(0.0, min(1.0, self.trust * DOWNWEIGHT.get(self.klass, 1.0)))


class TrustRegistry:
    """Loaded registry: handle -> :class:`ChannelTrust`."""

    def __init__(self, entries: dict[str, ChannelTrust]) -> None:
        self.entries = entries

    def get(self, handle: str) -> ChannelTrust:
        return self.entries.get(normalize(handle), ChannelTrust(handle=normalize(handle)))

    def news_eligible(self, handle: str) -> bool:
        return self.get(handle).news_eligible

    def weight(self, handle: str) -> float:
        return self.get(handle).weight

    def is_monitored_only(self, handle: str) -> bool:
        return self.get(handle).monitored_only

    def news_sources(self, handles: Iterable[str]) -> list[str]:
        """Keep only handles usable as news sources, preserving input order."""
        return [h for h in handles if self.news_eligible(h)]


def _load(path: Path) -> dict[str, ChannelTrust]:
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    entries: dict[str, ChannelTrust] = {}
    for row in raw.get("channels", []):
        klass = str(row.get("class", "unknown"))
        if klass not in CLASSES:
            log.warning(
                "channel_trust: unknown class %r for %r — skipped", klass, row.get("handle")
            )
            continue
        handle = normalize(row.get("handle", ""))
        if not handle:
            continue
        entries[handle] = ChannelTrust(
            handle=handle,
            title=str(row.get("title") or ""),
            subscribers=row.get("subscribers"),
            klass=klass,
            trust=float(row.get("trust", 0.5)),
            monitored_only=bool(row.get("monitored_only", False)),
            evidence=tuple(row.get("evidence") or ()),
        )
    return entries


def load_channel_trust(path: str | os.PathLike[str] | None = None) -> TrustRegistry:
    """Load the registry (env ``RMI_CHANNEL_TRUST_PATH`` overrides the default)."""
    target = Path(path or os.environ.get(ENV_PATH) or DEFAULT_PATH)
    try:
        return TrustRegistry(_load(target))
    except FileNotFoundError:
        log.warning("channel_trust: registry missing at %s — every channel scores unknown", target)
        return TrustRegistry({})
