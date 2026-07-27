"""Voice handler — transcribes voice messages, sends to AI for response + optional scan."""

# ⚠️ DEAD CODE — NOT WIRED INTO MAIN BOT
# This module is NOT imported or registered by bot.py.
# Kept as reference for future migration to rmi-backend.
# DO NOT add production credentials to this file.

import logging
import os
import re
import tempfile

import httpx
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

logger = logging.getLogger("telegram.voice")

# Cache Whisper model globally (load once, reuse)
_whisper = None


def _get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel

        _whisper = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _whisper


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transcribe voice → scan if token detected, otherwise answer question."""
    voice = update.message.voice
    if not voice:
        return

    msg = await update.message.reply_text("🎤 Listening...")

    try:
        # Download voice file
        file = await voice.get_file()
        fd, path = tempfile.mkstemp(suffix=".ogg")
        os.close(fd)
        await file.download_to_drive(path)

        # Transcribe with local Whisper
        whisper = _get_whisper()
        segments, _ = whisper.transcribe(path)
        text = " ".join([s.text.strip() for s in segments]).strip()

        if not text:
            await msg.edit_text(
                "🎤 Didn't catch that. Please try again or type your message."
            )
            return

        # Check if text contains a token address
        addr_match = re.search(
            r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b|0x[a-fA-F0-9]{40}|T[a-zA-Z0-9]{33}", text
        )

        if addr_match:
            address = addr_match.group()
            chain = (
                "ethereum"
                if address.startswith("0x")
                else "tron"
                if address.startswith("T")
                else "solana"
            )

            await msg.edit_text(
                f"🎤 *You said:* {text[:150]}\n\n🔍 Scanning `{address[:12]}...`",
                parse_mode=ParseMode.MARKDOWN,
            )

            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    "http://127.0.0.1:8000/scanner/scan",
                    json={"chain": chain, "address": address, "depth": "quick"},
                )
                if r.status_code == 200:
                    data = r.json()
                    risk = data.get("risk_score", 50)
                    tier = data.get("risk_tier", "unknown")
                    icon = (
                        "🟢"
                        if risk < 20
                        else "🟡"
                        if risk < 40
                        else "🟠"
                        if risk < 60
                        else "🔴"
                        if risk < 80
                        else "⛔"
                    )

                    response = f"{icon} *Voice Scan*\n`{address[:12]}...` ({chain})\nRisk: {risk}/100 ({tier.upper()})\n"
                    for f in data.get("findings", [])[:3]:
                        response += f"⚠ {f}\n"
                    await msg.edit_text(response, parse_mode=ParseMode.MARKDOWN)
                else:
                    await msg.edit_text(f"🎤 Heard: {text}\n\n❌ Scan failed.")
        else:
            # Answer question via DeepSeek
            DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")
            await msg.edit_text("🤖 Thinking...")
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"},
                    json={
                        "model": "deepseek-v4-flash",
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are Rug Munch Bot. Answer crypto questions concisely.",
                            },
                            {"role": "user", "content": text},
                        ],
                        "max_tokens": 300,
                    },
                )
                if r.status_code == 200:
                    answer = (
                        r.json()
                        .get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )
                    response = f"🎤 *You:* {text}\n\n🤖 {answer}"
                    await msg.edit_text(response[:2000], parse_mode=ParseMode.MARKDOWN)
                else:
                    await msg.edit_text(
                        f"🎤 Heard: {text}\n\n❌ Couldn't process question."
                    )

    except Exception as e:
        logger.error(f"Voice error: {e}")
        await msg.edit_text(
            "🎤 Voice processing failed. Please type your message instead."
        )

    finally:
        try:
            os.unlink(path)
        except:
            pass


handlers = [MessageHandler(filters.VOICE, handle_voice)]
