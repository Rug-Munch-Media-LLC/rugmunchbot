from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MainDraft:
    text: str
    item: dict[str, Any]
    dedupe_key: str
    content_type: str | None
