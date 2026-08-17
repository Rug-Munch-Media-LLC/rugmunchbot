# VENDORED 2026-08-17 from rmi-backend/app/osint/face/opencv_engine.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Engine 5: opencv (cv2, cheap fallback) with the committed models."""

from __future__ import annotations

from typing import Any

import numpy as np

from rugmunchbot._vendor.osint.face._common import (
    HAARCASCADE_MODEL_PATH,
    YUNET_MODEL_PATH,
    _bbox_payload,
    _load_rgb,
    logger,
)


def _detect_opencv(image_path: str) -> list[dict[str, Any]]:
    """Cheap OpenCV face detection using the committed models (no downloads).

    Prefers ``cv2.FaceDetectorYN`` (YuNet ONNX — the only face detector in
    opencv-python 5.x, which dropped ``CascadeClassifier``) and falls back to
    ``cv2.CascadeClassifier`` (Haar XML — classic OpenCV 4.x). Runs only when
    the heavy engines are unavailable or found zero faces. Returns
    bounding-box-only results (no embeddings) so a face count / locations are
    still produced on any machine with ``cv2`` + the committed models.
    """
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover — availability checked by caller
        logger.debug("cv2 not installed: %s", exc)
        return []
    img = _load_rgb(image_path)
    if img is None:
        return []

    # Preferred: YuNet (OpenCV 5.x). BGR conversion + contiguous copy for DNN.
    if YUNET_MODEL_PATH.is_file() and hasattr(cv2, "FaceDetectorYN"):
        try:
            detector = cv2.FaceDetectorYN.create(
                str(YUNET_MODEL_PATH),
                "",
                (img.shape[1], img.shape[0]),
                score_threshold=0.9,
                nms_threshold=0.3,
                top_k=5000,
            )
            _ok, faces = detector.detect(np.ascontiguousarray(img[:, :, ::-1]))
            rects: list[tuple[float, float, float, float]] = []
            if faces is not None and len(faces):
                # YuNet rows are [x, y, w, h, landmarks..., score] — convert to
                # (x1, y1, x2, y2) for _bbox_payload.
                rects = [
                    (float(f[0]), float(f[1]), float(f[0]) + float(f[2]), float(f[1]) + float(f[3])) for f in faces
                ]
            if rects:
                return [
                    {
                        "bbox": _bbox_payload(x, y, x2, y2),
                        "embedding": {},
                        "landmarks": [],
                        "det_score": None,
                    }
                    for (x, y, x2, y2) in rects
                ]
        except Exception as exc:
            logger.warning("yunet detection failed, trying haar: %s", exc)

    # Fallback: Haar cascade (classic OpenCV 4.x).
    if HAARCASCADE_MODEL_PATH.is_file() and hasattr(cv2, "CascadeClassifier"):
        try:
            cascade = cv2.CascadeClassifier(str(HAARCASCADE_MODEL_PATH))
            if cascade.empty():
                logger.warning("haarcascade model failed to load: %s", HAARCASCADE_MODEL_PATH)
                return []
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            rects_haar = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
            return [
                {
                    "bbox": _bbox_payload(float(x), float(y), float(x + w), float(y + h)),
                    "embedding": {},
                    "landmarks": [],
                    "det_score": None,
                }
                for (x, y, w, h) in rects_haar
            ]
        except Exception as exc:
            logger.warning("haarcascade detection failed: %s", exc)
            return []

    if not YUNET_MODEL_PATH.is_file() and not HAARCASCADE_MODEL_PATH.is_file():
        logger.warning("no face model committed under app/osint/models/")
    return []
