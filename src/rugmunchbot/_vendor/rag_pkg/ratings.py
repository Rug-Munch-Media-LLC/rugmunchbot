# VENDORED 2026-08-24 from rmi-backend/app/rag/ratings.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Article rating stub — rating pipeline lives in rugbrain."""

from typing import Any


async def rate_article(article: dict[str, Any]) -> dict[str, Any]:
    """Stub: article rating has been migrated to rugbrain."""
    return {"score": 0.5, "rated": False, "source": "rugbrain"}
