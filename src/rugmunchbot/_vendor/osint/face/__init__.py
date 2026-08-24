# VENDORED 2026-08-24 from rmi-backend/app/osint/face/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Facial-recognition OSINT — face detection, embeddings, reverse-image search.

Contract (see :mod:`app.osint`): ``lookup_face(image_path, *, timeout=120) ->
OsintReport``.

Runs four face-detection/recognition engines in strict fallback order — the
first engine that finds at least one face wins, and each engine failure
(missing package, model-download error, zero faces detected) falls through to
the next:

1. ``insightface``      — ArcFace ``buffalo_l`` ONNX models (auto-downloaded on
   first use; requires ``onnxruntime``), 512-d embeddings
   (:mod:`app.osint.face.insightface_engine`).
2. ``deepface``         — Facenet512 embeddings (VGG-Face selectable), 512-d
   (:mod:`app.osint.face.deepface_engine`).
3. ``face_recognition`` — dlib-based HOG detector, 128-d encodings
   (:mod:`app.osint.face.face_recognition_engine`).
4. ``compreface``       — self-hosted CompreFace (remote REST API, see
   :mod:`app.osint.compreface_client` + ``infra/compreface/``). Detection +
   subject matching against a collection; matched faces carry ``subject`` /
   ``similarity`` in the finding data. Only enabled when ``COMPREFACE_URL``
   and ``COMPREFACE_API_KEY`` are set
   (:mod:`app.osint.face.compreface_engine`).
5. ``opencv``           — cheap cv2 fallback with the committed Haar/YuNet
   models (:mod:`app.osint.face.opencv_engine`).

The first three are heavy optional dependencies; ``compreface`` needs only the
(already-shipped) httpx client plus a reachable CompreFace server. The package
imports cleanly without any of them and raises :class:`OsintNotAvailable` only
when a lookup is attempted with none installed. Models auto-download on first
use and can take minutes on a cold start; ``timeout`` is accepted for
interface parity with the other OSINT modules but engine calls are synchronous
CPU/network work and may exceed it on first use.

Reverse-image search: the report always carries a ``reverse_search`` finding
with ready-to-open URLs for Google Lens, Google search-by-image, Bing Visual
Search, and Yandex. Those engines require the image to be reachable at a
public URL — pass one (e.g. ``https://example.com/pic.jpg``) to get clickable
links; for a local file the module reports that the image must be hosted
first. Detection itself requires a local file path; URL inputs skip the face
engines (documented limitation).

Error handling:
  * No engine installed          -> raises :class:`OsintNotAvailable`.
  * Missing/undecodable image    -> raises :class:`OsintError`.
  * Engines installed, no faces  -> report with a single low-confidence
    "no face detected" finding plus per-engine notes in ``report.errors``.
  * Engine exceptions            -> logged, appended to ``report.errors``, and
    the next engine is tried — a lookup never raises for an engine failure.

Usage::

    from rugmunchbot._vendor.osint.face import lookup_face

    report = lookup_face("/tmp/face.jpg", timeout=120)
    for finding in report.findings:
        print(finding.source, finding.category, finding.summary, finding.confidence)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from rugmunchbot._vendor.osint import OsintError, OsintFinding, OsintNotAvailable, OsintReport
from rugmunchbot._vendor.osint.face._common import (
    _ENGINE_ORDER,
    HAARCASCADE_MODEL_PATH,
    YUNET_MODEL_PATH,
    _engine_available,
    _face_confidence,
    _reverse_search_urls,
    logger,
)
from rugmunchbot._vendor.osint.face.compreface_engine import _detect_compreface
from rugmunchbot._vendor.osint.face.deepface_engine import _detect_deepface
from rugmunchbot._vendor.osint.face.face_recognition_engine import _detect_face_recognition
from rugmunchbot._vendor.osint.face.insightface_engine import _detect_insightface
from rugmunchbot._vendor.osint.face.opencv_engine import _detect_opencv

#: Engine dispatch — first entry in _ENGINE_ORDER with a non-empty result wins.
_ENGINES: Final[dict[str, Any]] = {
    "insightface": _detect_insightface,
    "deepface": _detect_deepface,
    "face_recognition": _detect_face_recognition,
    "compreface": _detect_compreface,
    "opencv": _detect_opencv,
}

__all__ = ["HAARCASCADE_MODEL_PATH", "YUNET_MODEL_PATH", "lookup_face"]


def lookup_face(image_path: str, *, timeout: int = 120) -> OsintReport:
    """Detect faces in an image and produce a facial-recognition OSINT report.

    Args:
        image_path: Local path to an image file (JPEG/PNG/WebP). A public
            ``http(s)://`` URL is accepted for reverse-image-search links only;
            face detection requires a local file (documented limitation).
        timeout: Accepted for interface parity with the other OSINT modules.
            Engine calls are synchronous and first-use model downloads can
            exceed this; the value is not currently applied per engine.

    Returns:
        An :class:`OsintReport` with one ``face`` finding per detected face
        (source = winning engine, data includes bbox, truncated/hashed
        embedding, landmarks) plus a ``reverse_search`` finding with
        ready-to-open URLs (or a note that the image must be hosted first).

    Raises:
        OsintNotAvailable: if no face engine is available (none of the
            optional packages installed, no committed opencv model, and no
            CompreFace server configured).
        OsintError: if the local image file does not exist or is undecodable.
    """
    report = OsintReport(target=image_path, target_type="face")

    is_public_url = image_path.startswith(("http://", "https://"))
    if not is_public_url and not Path(image_path).is_file():
        raise OsintError(f"image file not found: {image_path}")

    available = [name for name in _ENGINE_ORDER if _engine_available(name)]
    if not available:
        raise OsintNotAvailable(
            "no face-recognition engine installed; run: pip install insightface deepface face_recognition "
            "(or install opencv-python with the committed Haar cascade model, "
            "or set COMPREFACE_URL + COMPREFACE_API_KEY for a CompreFace server — "
            "see infra/compreface/README.md)"
        )

    engines_tried: list[str] = []
    failures: list[str] = []
    detected: list[tuple[str, dict[str, Any]]] = []

    if is_public_url:
        failures.append("detection requires a local file path; skipping face engines for URL input")
    else:
        for name in _ENGINE_ORDER:
            if not _engine_available(name):
                if name == "opencv":
                    failures.append("opencv: cv2 not installed or no committed face model under app/osint/models/")
                elif name == "compreface":
                    failures.append(
                        "compreface: COMPREFACE_URL / COMPREFACE_API_KEY not set (see infra/compreface/README.md)"
                    )
                else:
                    failures.append(f"{name}: package not installed")
                continue
            engines_tried.append(name)
            try:
                faces = _ENGINES[name](image_path)
            except Exception as exc:  # engine bug — never abort the lookup
                logger.warning("face engine %s raised: %s", name, exc)
                failures.append(f"{name}: {exc}")
                continue
            if faces:
                detected.extend((name, face) for face in faces)
                break
            logger.info("face engine %s found 0 faces; falling through", name)

    # -- face findings ------------------------------------------------------
    # Engine failures are surfaced in report.errors even when a later engine
    # succeeds, matching the per-source degradation pattern of the other OSINT
    # modules (see app/osint/domain/__init__.py).
    report.errors.extend(failures)
    if detected:
        for name, face in detected:
            report.findings.append(
                OsintFinding(
                    source=name,
                    category="face",
                    summary="1 face detected",
                    confidence=_face_confidence(face),
                    data=face,
                )
            )
    else:
        report.findings.append(
            OsintFinding(
                source="face_detection",
                category="face",
                summary="No face detected",
                confidence="low",
                data={"engines_tried": engines_tried, "failures": failures},
            )
        )

    # -- reverse-image search ------------------------------------------------
    if is_public_url:
        report.findings.append(
            OsintFinding(
                source="reverse_image_search",
                category="reverse_search",
                summary="Reverse-image search links generated for the public image URL",
                confidence="high",
                data={"urls": _reverse_search_urls(image_path)},
            )
        )
    else:
        report.findings.append(
            OsintFinding(
                source="reverse_image_search",
                category="reverse_search",
                summary="Reverse-image search unavailable: image must be hosted at a public URL first",
                confidence="low",
                data={
                    "urls": {},
                    "requires_public_url": True,
                    "note": (
                        "Google Lens / Bing / Yandex need the image reachable at a public URL. "
                        "Host the file (e.g. S3, imgur) and re-run with the https:// URL."
                    ),
                },
            )
        )

    return report
