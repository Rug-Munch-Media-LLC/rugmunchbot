# VENDORED 2026-08-24 from rmi-backend/app/osint/compreface_client.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""CompreFace self-hosted face-recognition client.

CompreFace (Exadel, Apache-2.0, https://github.com/exadel-inc/CompreFace) is a
self-hosted face-recognition platform shipped as Docker images (core + admin +
api; see ``infra/compreface/``). It exposes a REST API for face detection,
recognition against a subject collection, and enrollment. This client talks to
the *face recognition service* endpoint (``/api/v1/recognition/*``) — the API
key used is the per-service ``x-api-key`` created in the CompreFace admin UI /
management API.

REST contract (CompreFace 1.2.0, ``docs/Rest-API-description.md``):

    POST /api/v1/recognition/faces?subject=<name>   enroll a face (multipart ``file``)
    POST /api/v1/recognition/recognize              detect + match faces (multipart ``file``)
    POST /api/v1/detection/detect                   detect-only (multipart ``file``)

All requests carry ``x-api-key: <service_api_key>``. The service returns JSON
of the form ``{"result": [ ... ]}`` where each face has ``box``
(``{probability, x_min, y_min, x_max, y_max}``) and, for recognition,
``subjects`` (``[{subject, similarity}]`` ordered by similarity).

Environment:

    COMPREFACE_URL       base URL of the CompreFace API, including scheme
                         (default ``http://127.0.0.1:8000`` — the loopback
                         port the compose stack publishes)
    COMPREFACE_API_KEY   the face-recognition service API key (``x-api-key``);
                         no default — required before any call

Error handling — graceful by design (mirrors ``app.osint.spiderfoot_client``):

    * ``COMPREFACE_API_KEY`` unset
      -> :class:`CompreFaceNotAvailable` (subclass of
         :class:`app.osint.OsintNotAvailable`).
    * Server unreachable / timeout / HTTP error
      -> :class:`CompreFaceNotAvailable` with the underlying cause.
    * Response missing ``result`` / malformed JSON
      -> logged, treated as an empty result (never raises).

Usage::

    from rugmunchbot._vendor.osint.compreface_client import add_face, is_configured, recognize

    assert is_configured()
    add_face("/tmp/alice.jpg", subject="alice")
    for hit in recognize("/tmp/unknown.jpg"):
        print(hit["subject"], hit["similarity"])
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, cast

import httpx

from rugmunchbot._vendor.osint import OsintNotAvailable

logger = logging.getLogger(__name__)

__all__ = [
    "CompreFaceNotAvailable",
    "add_face",
    "detect",
    "is_configured",
    "recognize",
]

# ─── Tunables ────────────────────────────────────────────────────────

#: Base URL of the CompreFace API (published on loopback by the compose stack).
DEFAULT_COMPREFACE_URL = "http://127.0.0.1:8000"

#: Default per-call timeout — CompreFace core runs FaceNet inference server-side,
#: a cold start can take several seconds per request.
DEFAULT_TIMEOUT: float = 60.0

#: Maximum number of faces the API will process; 0 = no limit.
_DEFAULT_LIMIT: int = 0

#: Predictions per face (most similar subjects returned).
_DEFAULT_PREDICTION_COUNT: int = 1


class CompreFaceNotAvailable(OsintNotAvailable):
    """Raised when CompreFace is unconfigured or unreachable.

    Subclasses :class:`app.osint.OsintNotAvailable` so callers that already
    handle the OSINT availability contract keep working unchanged.
    """


# ─── Configuration ───────────────────────────────────────────────────


def _env_url() -> str:
    """Resolve the CompreFace base URL from the environment."""
    return os.environ.get("COMPREFACE_URL", DEFAULT_COMPREFACE_URL).rstrip("/")


def _env_api_key() -> str | None:
    """Resolve the face-recognition service API key from the environment."""
    key = os.environ.get("COMPREFACE_API_KEY", "").strip()
    return key or None


def is_configured() -> bool:
    """Return True when ``COMPREFACE_URL`` and ``COMPREFACE_API_KEY`` are set.

    Presence check only — the service itself may still be down; a call will
    then raise :class:`CompreFaceNotAvailable`.
    """
    return _env_api_key() is not None and bool(_env_url())


def _require_api_key() -> str:
    """Return the configured API key or raise :class:`CompreFaceNotAvailable`."""
    key = _env_api_key()
    if key is None:
        raise CompreFaceNotAvailable(
            "CompreFace API key not configured; set COMPREFACE_API_KEY "
            "(create a face-recognition service via the CompreFace admin API — "
            "see infra/compreface/README.md)"
        )
    return key


# ─── Payload helpers ─────────────────────────────────────────────────


def _to_float(value: Any) -> float | None:
    """Coerce an API value to ``float``, or ``None`` when not numeric."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _box_payload(box: Any) -> dict[str, int | float]:
    """Normalize a CompreFace ``box`` object to ``{x1, y1, x2, y2, w, h}``.

    CompreFace reports integer pixel coordinates. Returns an empty dict when
    the box is missing or malformed.
    """
    if not isinstance(box, dict):
        return {}
    x1 = _to_float(box.get("x_min"))
    y1 = _to_float(box.get("y_min"))
    x2 = _to_float(box.get("x_max"))
    y2 = _to_float(box.get("y_max"))
    if x1 is None or y1 is None or x2 is None or y2 is None:
        return {}
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


def _box_and_probability(face: dict[str, Any]) -> tuple[dict[str, int | float], float | None]:
    """Return ``(bbox payload, detector probability)`` for a response face."""
    box = face.get("box")
    bbox = _box_payload(box)
    probability: float | None = None
    if isinstance(box, dict):
        probability = _to_float(box.get("probability"))
    return bbox, probability


def _result_list(payload: Any) -> list[dict[str, Any]]:
    """Coerce an API response to a list of face dicts (empty on malformed)."""
    if not isinstance(payload, dict):
        return []
    body = cast("dict[str, Any]", payload)
    result = body.get("result")
    if not isinstance(result, list):
        logger.warning("compreface: response has no 'result' list: %r", str(body)[:200])
        return []
    faces: list[dict[str, Any]] = []
    for item in result:
        if isinstance(item, dict):
            faces.append(cast("dict[str, Any]", item))
    return faces


def _face_payload(face: dict[str, Any]) -> dict[str, Any]:
    """Map one recognition face to ``{subject, similarity, all_subjects, bbox}``."""
    bbox, probability = _box_and_probability(face)
    subjects = face.get("subjects")
    matches: list[dict[str, Any]] = []
    if isinstance(subjects, list):
        for entry in subjects:
            if isinstance(entry, dict) and entry.get("subject"):
                matches.append(cast("dict[str, Any]", entry))
    top = matches[0] if matches else None
    payload: dict[str, Any] = {
        "subject": top.get("subject") if top else None,
        "similarity": top.get("similarity") if top else None,
        "all_subjects": matches,
        "bbox": bbox,
    }
    if probability is not None:
        payload["probability"] = probability
    return payload


# ─── API calls ───────────────────────────────────────────────────────


def _post_image(path: str, endpoint: str, *, params: dict[str, Any] | None = None) -> Any:
    """POST an image file to a CompreFace endpoint; returns the parsed JSON.

    Raises:
        CompreFaceNotAvailable: when the API key is unset or the service is
            unreachable (connection error, timeout, non-2xx HTTP status).
    """
    api_key = _require_api_key()
    url = f"{_env_url()}{endpoint}"
    with open(path, "rb") as handle:
        files = {"file": (Path(path).name, handle)}
        headers = {"x-api-key": api_key}
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.post(url, params=params, files=files, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CompreFaceNotAvailable(f"CompreFace unreachable at {url}: {exc}") from exc
    try:
        return response.json()
    except ValueError as exc:  # pragma: no cover — server returned non-JSON
        logger.warning("compreface: non-JSON response from %s: %s", url, exc)
        return {}


def add_face(image: str, subject: str, *, det_prob_threshold: float | None = None) -> dict[str, Any]:
    """Enroll a face image into the collection under ``subject``.

    Args:
        image: Local path to a single-face image (jpeg/png/webp/...).
        subject: Name the face is stored under (person alias, wallet label,
            case ID, ...). The subject is created automatically on first use.
        det_prob_threshold: Optional minimum detector confidence (0.0-1.0).

    Returns:
        The parsed response, e.g. ``{"image_id": "...", "subject": "..."}``.

    Raises:
        CompreFaceNotAvailable: if CompreFace is unconfigured or unreachable.
    """
    params: dict[str, Any] = {"subject": subject}
    if det_prob_threshold is not None:
        params["det_prob_threshold"] = str(det_prob_threshold)
    payload = _post_image(image, "/api/v1/recognition/faces", params=params)
    if not isinstance(payload, dict):
        raise CompreFaceNotAvailable(f"CompreFace add_face returned an unexpected response: {payload!r}")
    return cast("dict[str, Any]", payload)


def recognize(
    image: str,
    *,
    prediction_count: int = _DEFAULT_PREDICTION_COUNT,
    limit: int = _DEFAULT_LIMIT,
    det_prob_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """Detect faces in ``image`` and match them against the collection.

    Args:
        image: Local path to an image (may contain multiple faces).
        prediction_count: Max subject predictions returned per face.
        limit: Max faces processed (0 = all).
        det_prob_threshold: Optional minimum detector confidence (0.0-1.0).

    Returns:
        One dict per detected face: ``{subject, similarity, all_subjects,
        bbox, probability}``. ``subject``/``similarity`` are ``None`` when the
        face matches nothing in the collection (or the collection is empty).

    Raises:
        CompreFaceNotAvailable: if CompreFace is unconfigured or unreachable.
    """
    params: dict[str, Any] = {"prediction_count": prediction_count, "limit": limit}
    if det_prob_threshold is not None:
        params["det_prob_threshold"] = str(det_prob_threshold)
    payload = _post_image(image, "/api/v1/recognition/recognize", params=params)
    return [_face_payload(face) for face in _result_list(payload)]


def detect(
    image: str,
    *,
    limit: int = _DEFAULT_LIMIT,
    det_prob_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """Detect faces in ``image`` without matching against the collection.

    Args:
        image: Local path to an image.
        limit: Max faces processed (0 = all).
        det_prob_threshold: Optional minimum detector confidence (0.0-1.0).

    Returns:
        One dict per detected face: ``{bbox, probability}``.

    Raises:
        CompreFaceNotAvailable: if CompreFace is unconfigured or unreachable.
    """
    params: dict[str, Any] = {"limit": limit}
    if det_prob_threshold is not None:
        params["det_prob_threshold"] = str(det_prob_threshold)
    payload = _post_image(image, "/api/v1/detection/detect", params=params)
    results: list[dict[str, Any]] = []
    for face in _result_list(payload):
        bbox, probability = _box_and_probability(face)
        entry: dict[str, Any] = {"bbox": bbox}
        if probability is not None:
            entry["probability"] = probability
        results.append(entry)
    return results
