"""/face — reply to a photo: detected faces + reverse-search hint."""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING

from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.handlers.osint._common import logger
from app.domains.telegram.rugmunchbot.handlers.osint._format import _error_text, _format_face_result
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.osint.face import HAARCASCADE_MODEL_PATH, YUNET_MODEL_PATH

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

# ─── Face detection (optional, degrades gracefully) ──────────────────


try:
    import cv2  # opencv-python ships a stub; attribute access is untyped below
except ImportError:  # pragma: no cover — optional dependency
    cv2 = None  # type: ignore[assignment]


def _detect_faces(image: bytes) -> tuple[int, str | None]:
    """Detect faces in an image with OpenCV's committed models.

    Uses ``cv2.FaceDetectorYN`` (YuNet ONNX — the only face detector in
    opencv-python 5.x, which dropped ``CascadeClassifier``) and falls back to
    ``cv2.CascadeClassifier`` (Haar XML — classic OpenCV 4.x). Both models are
    committed under ``app/osint/models/`` (opencv 5.x ships no cascades in
    ``cv2.data.haarcascades``). Returns ``(face_count, warning)``; ``warning``
    is set (and the count is 0) when OpenCV or both models are unavailable so
    callers can show a helpful message plus the reverse-search hint.
    """
    if cv2 is None:
        return 0, "face detection is not installed on this server (opencv-python missing)"
    try:
        import numpy as np

        arr = np.frombuffer(image, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return 0, "could not decode the photo"

        # Preferred: YuNet (OpenCV 5.x). DNN wants BGR, contiguous.
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
                _ok, faces = detector.detect(np.ascontiguousarray(img))
                if faces is not None and len(faces):
                    return len(faces), None
            except Exception as exc:
                logger.warning("yunet detection failed, trying haar: %s", exc)

        # Fallback: Haar cascade (classic OpenCV 4.x).
        if HAARCASCADE_MODEL_PATH.is_file() and hasattr(cv2, "CascadeClassifier"):
            cascade = cv2.CascadeClassifier(str(HAARCASCADE_MODEL_PATH))
            if not cascade.empty():
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
                return len(faces), None

        return 0, "face detection model not available on this server"
    except Exception as exc:
        logger.warning("face detection failed: %s", exc)
        return 0, "face detection failed for this photo"


async def cmd_face(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/face — reply to a photo: detected faces + reverse-search hint."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    reply = update.message.reply_to_message if update.message else None
    if reply is None or not reply.photo:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f5bc\ufe0f <b>Face Analysis</b>\n\n"
            "Usage: <b>reply to a photo</b> with /face\n\n"
            "Detects faces in the image and gives a reverse-search hint.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f50d <b>Analyzing photo</b>\u2026",
        parse_mode=ParseMode.HTML,
    )
    try:
        photo = reply.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        count, warning = await asyncio.to_thread(_detect_faces, buf.getvalue())
        await msg.edit_text(
            _format_face_result(count, warning),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=back_kb(),
        )
    except (TelegramError, ValueError, TimeoutError, OSError) as exc:
        await msg.edit_text(_error_text("photo", exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
