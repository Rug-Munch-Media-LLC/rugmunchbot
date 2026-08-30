"""Shill screenshot capture + vision OCR — evidence-grade shill recording.

2026-08-29: shills delete tweets. Text-only rails miss the deleted post and
the operator asked for screenshot evidence. This module captures a post
(Twitter/X status or Telegram message) as a PNG via Playwright, then sends
the image to DeepInfra Qwen3-VL-30B-A3B for OCR (extract text, tickers,
wallet addresses, handles) so the Wall of Shame can attach PROOF that
survives deletion.

Design:
* Screenshot is best-effort — X is Cloudflare-hardened and login-walled, so
  we render the public ``x.com/<handle>/status/<id>`` page and capture what
  renders; if the page is a login wall, we capture the fallback (nitter /
  fxtwitter / direct image URL) instead. Fail-open: no screenshot → still
  return whatever text we have.
* Vision OCR is the DeepInfra VL-30B lane (reasoning.enabled=false). Never
  raises; returns "" on any failure so callers degrade to text-only.
* Key resolution: DEEPINFRA_API_KEY env → $DEEPINFRA_KEY_FILE (default
  $RMI_SECRETS_DIR/deepinfra_api_key).
"""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx

DEEPINFRA_OPENAI = os.getenv("DEEPINFRA_BASE", "https://api.deepinfra.com/v1/openai")
# 2026-09-01: default vision model is the operator-specified Qwen3-VL-30B-A3B
# (DeepInfra) — MoE, ~3B active params, nails shill OCR (tickers/handles/
# addresses) while staying cheap. Tune via DEEPINFRA_VISION_MODEL; to route
# the OpenRouter GLM-5V-Turbo lane (``z-ai/glm-5v-turbo``) instead, set
# DEEPINFRA_BASE to the OpenRouter OpenAI-compatible endpoint and
# DEEPINFRA_API_KEY to the OpenRouter key (vision_ocr posts to
# ``{DEEPINFRA_BASE}/chat/completions``). [REVIEW] OpenRouter GLM fallback is
# not yet auto-wired — the DeepInfra lane is the canonical cheap-vision path.
DEEPINFRA_VISION_MODEL = os.getenv("DEEPINFRA_VISION_MODEL", "Qwen/Qwen3-VL-30B-A3B-Instruct")
VISION_TIMEOUT = float(os.getenv("DEEPINFRA_VISION_TIMEOUT", "60"))
#: Canonical key file for the DeepInfra key (never in git). Defaults to the
#: fleet secret-store location; overridable so tests/containers can point
#: elsewhere.
_KEY_FILE = os.getenv("DEEPINFRA_KEY_FILE") or os.path.join(
    os.getenv("RMI_SECRETS_DIR", "/srv/secrets"), "deepinfra_api_key"
)


def _load_key() -> str:
    key = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(_KEY_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


async def capture_screenshot(url: str, out_path: str, *, timeout_ms: int = 20000) -> bool:
    """Capture ``url`` as a PNG at ``out_path`` via Playwright. Returns success.

    Playwright is installed on this host (v1.62). The chromium browser must
    be installed once (``playwright install chromium``). Fail-open: any error
    → False, caller falls back to text-only evidence.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            await page.screenshot(path=out_path, full_page=False)
            await browser.close()
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
    except Exception:
        return False


async def vision_ocr(image_path: str, prompt: str | None = None) -> str:
    """Send a PNG to DeepInfra VL-30B and return its OCR/analysis text.

    ``prompt`` defaults to a shill-extraction instruction (text + tickers +
    wallet addresses + handles). Returns "" on any failure (fail-open).
    """
    key = _load_key()
    if not key:
        return ""
    if not os.path.isfile(image_path):
        return ""
    try:
        with open(image_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
    except OSError:
        return ""
    prompt = prompt or (
        "Extract from this screenshot of a crypto shill post: the full text, "
        "any token tickers/$SYMBOLS, any wallet addresses (solana base58 or "
        "0x…), and any @handles. Return plain text, one field per line."
    )
    payload: dict[str, Any] = {
        "model": DEEPINFRA_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": 600,
        "reasoning": {"enabled": False},
    }
    try:
        async with httpx.AsyncClient(timeout=VISION_TIMEOUT) as client:
            resp = await client.post(
                f"{DEEPINFRA_OPENAI}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
        if resp.status_code != 200:
            return ""
        return resp.json()["choices"][0]["message"].get("content", "") or ""
    except Exception:
        return ""


async def capture_and_ocr(
    url: str, *, out_path: str = "/tmp/opencode/shill_shot.png"
) -> dict[str, str]:
    """Convenience: capture a URL then OCR it. Returns {url, image_path, text}."""
    ok = await capture_screenshot(url, out_path)
    text = await vision_ocr(out_path) if ok else ""
    return {
        "url": url,
        "image_path": out_path if ok else "",
        "text": text,
        "captured": "true" if ok else "false",
    }
