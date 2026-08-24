# VENDORED 2026-08-24 from rmi-backend/app/osint/face/deepface_engine.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Engine 2: deepface (Facenet512) face detection + embeddings."""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.osint.face._common import (
    _DEEPFACE_MODEL,
    _bbox_payload,
    _embedding_payload,
    logger,
)


def _deepface_embedding(face_crop: Any) -> dict[str, Any]:
    """Embed a single face crop with deepface; ``{}`` on any failure."""
    try:
        from deepface import DeepFace
    except ImportError as exc:  # pragma: no cover — optional dep
        logger.debug("deepface not installed: %s", exc)
        return {}
    try:
        # "skip" avoids re-detection on the already-cropped face (deepface >= 0.0.80).
        reps = DeepFace.represent(
            img_path=face_crop,
            model_name=_DEEPFACE_MODEL,
            detector_backend="skip",
            enforce_detection=False,
        )
    except Exception:
        try:  # older deepface has no "skip" backend — fall back to default detection
            reps = DeepFace.represent(img_path=face_crop, model_name=_DEEPFACE_MODEL, enforce_detection=False)
        except Exception as exc:
            logger.warning("deepface represent failed: %s", exc)
            return {}
    if not reps:
        return {}
    return _embedding_payload(reps[0].get("embedding"))


def _detect_deepface(image_path: str) -> list[dict[str, Any]]:
    """Detect + embed faces with deepface (``extract_faces`` + ``represent``)."""
    try:
        from deepface import DeepFace
    except ImportError as exc:  # pragma: no cover — optional dep
        logger.debug("deepface not installed: %s", exc)
        return []
    try:
        extracted = DeepFace.extract_faces(
            img_path=image_path,
            detector_backend="opencv",  # lightweight, no extra model download
            enforce_detection=False,
        )
    except Exception as exc:
        logger.warning("deepface extract_faces failed: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for item in extracted:
        area = item.get("facial_area") or {}
        x, y, w, h = area.get("x", 0), area.get("y", 0), area.get("w", 0), area.get("h", 0)
        if not w or not h:
            continue
        landmarks: list[dict[str, Any]] = []
        for key in ("left_eye", "right_eye"):
            eye = area.get(key)
            if eye:
                landmarks.append(
                    {"name": key, "points": [{"x": round(float(eye[0]), 2), "y": round(float(eye[1]), 2)}]}
                )
        results.append(
            {
                "bbox": _bbox_payload(x, y, x + w, y + h),
                "embedding": _deepface_embedding(item.get("face")),
                "landmarks": landmarks,
                "det_score": float(item.get("confidence") or 0.0),
            }
        )
    return results
