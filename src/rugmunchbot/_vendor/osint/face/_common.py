# VENDORED 2026-08-17 from rmi-backend/app/osint/face/_common.py (Phase 2b — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Shared tunables + payload helpers for the face-recognition OSINT package.

Holds the engine fallback order, committed model paths, embedding truncation
settings and the payload builders shared by every engine module. Model paths
point at ``app/osint/models/`` (two levels up: this module lives in
``app/osint/face/_common.py``).
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import urllib.parse
from pathlib import Path
from typing import Any, Final

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

#: Engine fallback order — the first engine that detects >= 1 face wins.
#: ``compreface`` is the remote REST tier: it needs a configured, reachable
#: CompreFace server (``COMPREFACE_URL`` + ``COMPREFACE_API_KEY``) and is the
#: only engine that can also *identify* subjects, not just detect faces.
#: ``opencv`` is the cheap cv2 fallback (committed models, no package install /
#: model download): it runs only when the heavier engines are missing, found
#: zero faces, or CompreFace is unconfigured/unreachable.
_ENGINE_ORDER: Final[tuple[str, ...]] = (
    "insightface",
    "deepface",
    "face_recognition",
    "compreface",
    "opencv",
)

#: Committed Haar cascade for the cheap cv2 fallback. opencv-python 5.x ships
#: no cascade XMLs in ``cv2.data.haarcascades`` AND drops the classic
#: ``cv2.CascadeClassifier`` API, so the module tracks both the Haar XML
#: (used by ``cv2.CascadeClassifier`` on classic OpenCV 4.x installs) and the
#: YuNet ONNX model (used by ``cv2.FaceDetectorYN`` on OpenCV 5.x). Both live
#: in-repo at ``app/osint/models/`` and are shared with the Telegram bot
#: handler (``handlers/osint/face_cmd.py``).
HAARCASCADE_MODEL_PATH: Final[Path] = (
    Path(__file__).resolve().parent.parent / "models" / "haarcascade_frontalface_default.xml"
)
YUNET_MODEL_PATH: Final[Path] = Path(__file__).resolve().parent.parent / "models" / "face_detection_yunet_2023mar.onnx"

#: deepface recognition model (Facenet512 = 512-d; VGG-Face = 2622-d).
_DEEPFACE_MODEL: Final[str] = "Facenet512"

#: How many leading embedding dimensions are stored in the report (full vector
#: is never dumped — reports stay small; a sha256 fingerprint enables dedup).
_EMBEDDING_PREVIEW_DIMS: Final[int] = 32
_EMBEDDING_ROUND: Final[int] = 6

#: Face-recognition landmark features kept in the report (bounded size).
_FR_LANDMARK_FEATURES: Final[tuple[str, ...]] = (
    "left_eye",
    "right_eye",
    "nose_tip",
    "top_lip",
    "bottom_lip",
)

#: Reverse-image-search endpoints. Each takes the public image URL in the
#: ``{url}`` slot (percent-encoded when substituted).
_REVERSE_SEARCH_TEMPLATES: Final[dict[str, str]] = {
    "google_lens": "https://lens.google.com/uploadbyurl?url={url}",
    "google_search": "https://www.google.com/searchbyimage?image_url={url}",
    "bing_visual_search": "https://www.bing.com/images/searchbyimage?cbir=sbi&imgurl={url}",
    "yandex": "https://yandex.com/images/search?rpt=imageview&url={url}",
}


# ─── Availability helpers ────────────────────────────────────────────


def _engine_available(name: str) -> bool:
    """Return True if the (optional) engine is usable.

    ``opencv`` is special: it needs only ``cv2`` plus at least one committed
    model file (YuNet ONNX or Haar XML) — no pip install, no model download.
    ``compreface`` is a remote service: availability is a *configuration*
    check (``COMPREFACE_URL`` + ``COMPREFACE_API_KEY`` set), not an import —
    reachability is only known at call time and is handled gracefully there
    (see :mod:`app.osint.compreface_client`).
    """
    if name == "opencv":
        return importlib.util.find_spec("cv2") is not None and (
            YUNET_MODEL_PATH.is_file() or HAARCASCADE_MODEL_PATH.is_file()
        )
    if name == "compreface":
        try:
            from rugmunchbot._vendor.osint.compreface_client import is_configured
        except ImportError as exc:  # pragma: no cover — module ships in-app
            logger.debug("compreface client unavailable: %s", exc)
            return False
        return is_configured()
    return importlib.util.find_spec(name) is not None


def _load_rgb(image_path: str) -> Any | None:
    """Load an image file as an RGB numpy array, or None if it cannot be decoded."""
    try:
        with Image.open(image_path) as im:
            return np.asarray(im.convert("RGB"))
    except Exception as exc:  # corrupt / unsupported file — engine falls through
        logger.warning("could not decode image %s: %s", image_path, exc)
        return None


# ─── Payload builders ────────────────────────────────────────────────


def _bbox_payload(x1: float, y1: float, x2: float, y2: float) -> dict[str, int]:
    """Normalize a bounding box into a bounded ``{x1, y1, x2, y2, w, h}`` payload."""
    left, top = round(x1), round(y1)
    right, bottom = round(x2), round(y2)
    return {
        "x1": left,
        "y1": top,
        "x2": right,
        "y2": bottom,
        "w": max(0, right - left),
        "h": max(0, bottom - top),
    }


def _embedding_payload(embedding: Any) -> dict[str, Any]:
    """Shrink a raw embedding ndarray into a bounded, hashable report payload.

    Stores a short numeric preview plus a deterministic sha256 of the full
    float32 vector (for dedup/comparison) — never the full embedding.
    """
    arr = np.asarray(embedding, dtype="<f4")
    if arr.size == 0:
        return {"preview": [], "dim": 0, "sha256": "", "truncated": False, "preview_dims": _EMBEDDING_PREVIEW_DIMS}
    digest = hashlib.sha256(arr.tobytes()).hexdigest()
    preview = [round(float(v), _EMBEDDING_ROUND) for v in arr.ravel()[:_EMBEDDING_PREVIEW_DIMS].tolist()]
    return {
        "preview": preview,
        "dim": int(arr.shape[0]),
        "sha256": digest,
        "truncated": bool(arr.shape[0] > _EMBEDDING_PREVIEW_DIMS),
        "preview_dims": _EMBEDDING_PREVIEW_DIMS,
    }


def _landmarks_payload(points: Any, max_points: int = 5) -> list[dict[str, Any]]:
    """Convert an Nx2 landmark array (insightface ``kps``) into ``[{x, y}]``."""
    arr = np.asarray(points, dtype="<f8")
    if arr.ndim != 2 or arr.shape[1] != 2:
        return []
    return [{"x": round(float(row[0]), 2), "y": round(float(row[1]), 2)} for row in arr[:max_points]]


def _fr_landmarks_payload(landmarks: Any, max_points: int = 4) -> list[dict[str, Any]]:
    """Convert face_recognition landmark dicts into a bounded feature list."""
    if not landmarks:
        return []
    out: list[dict[str, Any]] = []
    for feature in _FR_LANDMARK_FEATURES:
        pts = landmarks.get(feature) or []
        out.append(
            {
                "name": feature,
                "points": [{"x": round(float(p[0]), 2), "y": round(float(p[1]), 2)} for p in pts[:max_points]],
            }
        )
    return out


def _face_confidence(face: dict[str, Any]) -> str:
    """Map a detector score onto the report confidence scale."""
    score = face.get("det_score")
    if score is not None and float(score) >= 0.8:
        return "high"
    return "medium"


# ─── Reverse-image search ────────────────────────────────────────────


def _reverse_search_urls(public_url: str) -> dict[str, str]:
    """Build reverse-image-search URLs for a publicly reachable image URL."""
    encoded = urllib.parse.quote(public_url, safe="")
    return {name: template.format(url=encoded) for name, template in _REVERSE_SEARCH_TEMPLATES.items()}
