# VENDORED 2026-08-24 from rmi-backend/app/osint/face/face_recognition_engine.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Engine 3: face_recognition (dlib) detection + 128-d encodings."""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.osint.face._common import (
    _bbox_payload,
    _embedding_payload,
    _fr_landmarks_payload,
    logger,
)


def _detect_face_recognition(image_path: str) -> list[dict[str, Any]]:
    """Detect + encode faces with face_recognition (128-d dlib encodings)."""
    try:
        import face_recognition
    except ImportError as exc:  # pragma: no cover — optional dep
        logger.debug("face_recognition not installed: %s", exc)
        return []
    try:
        img = face_recognition.load_image_file(image_path)
    except Exception as exc:
        logger.warning("face_recognition could not load %s: %s", image_path, exc)
        return []
    try:
        locations = face_recognition.face_locations(img)  # (top, right, bottom, left)
        encodings = face_recognition.face_encodings(img, known_face_locations=locations)
        landmark_sets = face_recognition.face_landmarks(img, face_locations=locations)
    except Exception as exc:
        logger.warning("face_recognition detection failed: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for idx, (top, right, bottom, left) in enumerate(locations):
        emb = encodings[idx] if idx < len(encodings) else None
        landmarks = _fr_landmarks_payload(landmark_sets[idx] if idx < len(landmark_sets) else None)
        results.append(
            {
                "bbox": _bbox_payload(left, top, right, bottom),
                "embedding": _embedding_payload(emb) if emb is not None else {},
                "landmarks": landmarks,
                "det_score": None,
            }
        )
    return results
