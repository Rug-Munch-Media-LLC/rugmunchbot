# VENDORED 2026-08-17 from rmi-backend/app/osint/face/insightface_engine.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Engine 1: insightface (ArcFace / Buffalo) face detection + embeddings."""

from __future__ import annotations

from typing import Any

import numpy as np

from rugmunchbot._vendor.osint.face._common import (
    _bbox_payload,
    _embedding_payload,
    _landmarks_payload,
    _load_rgb,
    logger,
)


def _detect_insightface(image_path: str) -> list[dict[str, Any]]:
    """Detect + embed faces with insightface ``buffalo_l`` (512-d embeddings)."""
    try:
        from insightface.app import FaceAnalysis
    except ImportError as exc:  # pragma: no cover — optional dep
        logger.debug("insightface not installed: %s", exc)
        return []
    try:
        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(640, 640))
    except Exception as exc:  # model download / onnxruntime init failure
        logger.warning("insightface init failed: %s", exc)
        return []
    img = _load_rgb(image_path)
    if img is None:
        return []
    try:
        # insightface expects BGR (OpenCV convention); copy to get contiguous memory.
        faces = app.get(np.asarray(img)[:, :, ::-1].copy())
    except Exception as exc:
        logger.warning("insightface detection failed: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for face in faces:
        bbox = np.asarray(getattr(face, "bbox", []), dtype="<f8").ravel()
        if bbox.size < 4:
            continue
        results.append(
            {
                "bbox": _bbox_payload(bbox[0], bbox[1], bbox[2], bbox[3]),
                "embedding": _embedding_payload(getattr(face, "embedding", [])),
                "landmarks": _landmarks_payload(getattr(face, "kps", None)),
                "det_score": float(getattr(face, "det_score", 0.0) or 0.0),
            }
        )
    return results
