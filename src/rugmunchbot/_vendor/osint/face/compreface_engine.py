# VENDORED 2026-08-23 from rmi-backend/app/osint/face/compreface_engine.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Engine 4: compreface (self-hosted remote REST) detection + subject matching."""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.osint.face._common import logger


def _detect_compreface(image_path: str) -> list[dict[str, Any]]:
    """Detect + match faces via self-hosted CompreFace (``infra/compreface/``).

    This is the only engine that can *identify* a face against a subject
    collection, not just detect it. It runs the recognition endpoint, which
    internally runs the detector and returns every detected face — matched
    faces carry the top ``subject`` + ``similarity`` plus the full
    ``all_subjects`` prediction list; unmatched faces (or an empty collection)
    fall back to bounding-box-only results.

    Raises:
        CompreFaceNotAvailable: when the service is unconfigured or
            unreachable. Deliberately NOT swallowed here — the caller records
            it in ``report.errors`` and falls through to the next engine
            (mirrors how a broken local engine is reported).
    """
    try:
        from rugmunchbot._vendor.osint.compreface_client import recognize
    except ImportError as exc:  # pragma: no cover — client module ships in-app
        logger.debug("compreface client unavailable: %s", exc)
        return []

    matches = recognize(image_path, prediction_count=1, det_prob_threshold=0.5)
    results: list[dict[str, Any]] = []
    for match in matches:
        face: dict[str, Any] = {
            "bbox": match.get("bbox") or {},
            "embedding": {},
            "landmarks": [],
            "det_score": match.get("probability"),
        }
        subject = match.get("subject")
        if subject:
            face["subject"] = subject
            face["similarity"] = match.get("similarity")
            face["subjects"] = match.get("all_subjects") or []
        results.append(face)
    return results
