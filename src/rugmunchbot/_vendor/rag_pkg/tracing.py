# VENDORED 2026-08-24 from rmi-backend/app/rag/tracing.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Phoenix tracing — minimal stub. Full tracing in rugbrain."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator


def setup_phoenix() -> None:
    """Stub: Phoenix tracing lives in rugbrain."""


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Generator[Any, None, None]:
    """No-op span context manager (backend-side spans are not exported).

    Kept for API compatibility with app.rag.rerank / ann_index call sites;
    real OTEL spans are emitted by rugbrain.
    """

    class _NoopSpan:
        def set_attribute(self, *a: object, **k: object) -> None:
            return None

        def set_attributes(self, *a: object, **k: object) -> None:
            return None

        def record_exception(self, *a: object, **k: object) -> None:
            return None

    yield _NoopSpan()
