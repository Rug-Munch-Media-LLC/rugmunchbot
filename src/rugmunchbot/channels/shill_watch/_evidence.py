"""Shill evidence persistence (DB + Qdrant) + screenshot/OCR capture.

Split out of ``channels/shill_watch.py`` during the de-godding pass. A
screenshot is proof that survives deletion: we store the PNG reference + the
vision-OCR'd text (a) in a lightweight ``shill_evidence`` SQLite table and
(b) as a Qdrant point in the scam-intel collection (``threat_intel_v2``).
Also hosts the bounded X + Telegram KOL screenshot/OCR capture. Fail-open
everywhere: a missing dir, DB write error, or Qdrant outage never blocks the
alert.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from rugmunchbot.channels.shill_watch._config import (
    _QDRANT_URL,
    _SHILL_EVIDENCE_COLLECTION,
    _SHILL_OCR_MAX_CHARS,
    SHILL_SCREENSHOT_DIR,
    SHILL_SCREENSHOT_ENABLED,
    TG_CAPTURE_BUDGET,
    TG_CAPTURE_ENABLED,
    logger,
)
from rugmunchbot.channels.shill_watch._detect import (
    ShillCampaign,
    _article_fields,
    _tg_post_url,
    _x_status_url,
)


# ── Shill evidence persistence (DB + Qdrant, 2026-08-30) ───────────────────
# A screenshot is proof that survives deletion. We store the PNG reference +
# the vision-OCR'd text (a) in a lightweight ``shill_evidence`` SQLite table
# and (b) as a Qdrant point in the scam-intel collection (``threat_intel_v2``)
# so the bot can answer "who shilled $X / what did they post". Fail-open: a
# missing dir, DB write error, or Qdrant outage never blocks the alert.
def _ensure_shill_table() -> None:
    """Create the ``shill_evidence`` table (idempotent, fail-open)."""
    try:
        from rugmunchbot.db._base import get_db

        conn = get_db()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shill_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT,
                    chain TEXT,
                    account TEXT,
                    x_url TEXT,
                    image_path TEXT,
                    ocr_text TEXT,
                    captured_at TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_watch:shill_table_failed err=%s", str(exc)[:120])


async def _persist_shill_evidence(campaign: ShillCampaign, shot: dict[str, str]) -> bool:
    """Persist one screenshot's reference + OCR text to the backend evidence store.

    Shill evidence now lives in the BACKEND (``/api/v1/shill/evidence`` —
    Qdrant ``threat_intel_v2`` + the ``shill_evidence`` DB). This is the bot's
    thin-consumer path: it POSTs the capture to the backend first and only
    falls back to the legacy local DB/Qdrant write when the backend is
    unreachable (so evidence capture never drops offline). The return value
    is True when the evidence landed (backend or local). Never raises.
    """
    token = str(campaign.token or "")
    chain = str(campaign.chain or "")
    account = str(shot.get("account") or "")
    x_url = str(shot.get("url") or "")
    image_path = str(shot.get("image_path") or "")
    ocr = str(shot.get("text") or "")[:_SHILL_OCR_MAX_CHARS]
    # Capture origin ("x" | "telegram") — persisted so the site can
    # distinguish where a screenshot came from (2026-09-01).
    source = str(shot.get("source") or "").strip().lower()

    # ── Backend-first (thin consumer) ──
    try:
        from rugmunchbot.services import shill_api

        result = await shill_api.add_evidence(
            {
                "token": token,
                "chain": chain,
                "account": account,
                "x_url": x_url,
                "url": x_url,
                "image_path": image_path,
                "text": ocr,
                "ocr_text": ocr,
                "source": source,
            }
        )
        if result and result.get("saved"):
            logger.debug("shill_watch:evidence_backend_saved token=%s account=%s", token, account)
            return True
    except Exception as exc:  # fail-open — degrade to local write
        logger.debug("shill_watch:evidence_backend_failed err=%s", str(exc)[:120])

    # ── Legacy local fallback (offline / backend unreachable) ──
    saved = False
    if image_path or ocr:
        _ensure_shill_table()
        try:
            from rugmunchbot.db._base import get_db

            conn = get_db()
            try:
                conn.execute(
                    "INSERT INTO shill_evidence (token, chain, account, x_url, image_path, ocr_text, captured_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        token[:80],
                        chain[:40],
                        account[:60],
                        x_url[:240],
                        image_path[:400],
                        ocr,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                conn.commit()
                saved = True
            finally:
                conn.close()
        except Exception as exc:  # fail-open by contract
            logger.debug("shill_watch:db_evidence_failed err=%s", str(exc)[:120])

    if x_url or ocr:
        try:
            import hashlib
            import uuid

            from rugmunchbot.channels.telegram_news._dedup import _embed_candidate

            content = (
                f"Source: {account or 'unknown'}\nToken: {token}\nChain: {chain}\n"
                f"URL: {x_url}\nOCR: {ocr}"
            ).strip()
            digest = hashlib.sha256(f"{token}:{x_url}:{account}".encode()).hexdigest()
            point_id = uuid.UUID(int=int(digest, 16) % (2**96), version=4)
            vector = await _embed_candidate(content)
            if vector:
                payload = {
                    "doc_id": f"shill:{digest[:20]}",
                    "kind": "shill_evidence",
                    "source": source or account or "telegram_shill",
                    "token": token[:80],
                    "chain": chain[:40],
                    "url": x_url,
                    "image_path": image_path,
                    "ocr_text": ocr,
                    "captured_at": datetime.now(UTC).isoformat(),
                }
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.put(
                        f"{_QDRANT_URL}/collections/{_SHILL_EVIDENCE_COLLECTION}/points",
                        params={"wait": True},
                        json={
                            "points": [{"id": str(point_id), "vector": vector, "payload": payload}]
                        },
                    )
                if resp.status_code not in (200, 202):
                    logger.debug(
                        "shill_watch:qdrant_evidence_status=%d body=%s",
                        resp.status_code,
                        resp.text[:160],
                    )
        except Exception as exc:  # fail-open by contract
            logger.debug("shill_watch:qdrant_evidence_failed err=%s", str(exc)[:160])
    return saved


async def _capture_campaign_shills(campaign: ShillCampaign, budget: int) -> int:
    """Cheap screenshot + OCR for the campaign's X shills (bounded).

    One DeepInfra VL-4B vision call per shill mention (``tools.shill_shot``),
    capped at ``budget`` per campaign. Captures only mentions that carry an X
    status URL. Attaches ``image_path``/``text`` onto the mention and the
    campaign's ``screenshots`` list. Fail-open: any capture/OCR error is
    logged and skipped — the campaign still alerts on text alone.
    """
    if not SHILL_SCREENSHOT_ENABLED:
        return 0
    captured = 0
    try:
        from rugmunchbot.tools.shill_shot import capture_and_ocr
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_watch:shill_shot_import_failed err=%s", str(exc)[:120])
        return 0
    try:
        os.makedirs(SHILL_SCREENSHOT_DIR, exist_ok=True)
    except OSError as exc:  # fail-open — a read-only home must not crash the tick
        logger.debug("shill_watch:screenshot_dir_failed err=%s", str(exc)[:120])
        return 0
    for mention in campaign.mentions:
        if captured >= budget:
            break
        x_url = _x_status_url(str(mention.get("url") or ""))
        # 2026-09-01: Telegram KOL mentions are captured through the SAME
        # pipeline — the public ``t.me/s/<channel>/<id>`` web preview — and
        # persisted with ``source='telegram'`` so the site can distinguish
        # origin. X mentions keep ``source='x'``.
        tg_url = "" if x_url else _tg_post_url(str(mention.get("url") or ""))
        if not x_url and not tg_url:
            continue
        out_path = os.path.join(
            SHILL_SCREENSHOT_DIR,
            f"{campaign.key.replace(':', '_')[:48]}_{captured}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.png",
        )
        account = str(mention.get("channel") or "")
        shot = await capture_and_ocr(x_url or tg_url, out_path=out_path)
        # 2026-09-04: for X shills also capture the element-level tweet
        # screenshot (``capture_tweet_screenshot``) so the campaign record
        # carries the evidence path even when the vision-OCR lane is down.
        # Fail-open: a None path just leaves the OCR-only evidence in place.
        if x_url:
            tweet_shot = await capture_tweet_screenshot(x_url)
            if tweet_shot:
                shot["image_path"] = tweet_shot
        if shot.get("text"):
            shot["account"] = account
            shot["source"] = "telegram" if tg_url else "x"
            campaign.screenshots.append(
                {k: shot.get(k, "") for k in ("url", "image_path", "text", "account", "source")}
            )
            mention["image_path"] = shot.get("image_path", "")
            mention["ocr_text"] = str(shot.get("text", ""))[:_SHILL_OCR_MAX_CHARS]
            await _persist_shill_evidence(campaign, shot)
            captured += 1
    return captured


# ── Telegram KOL post capture (2026-09-01) ─────────────────────────────────
# Deleted-post detection: the sweep is POLL-based (``read_channel_messages``
# over the headless client) and never sees deletions. Detecting a deleted
# KOL post requires a Telethon ``events.MessageDeleted`` handler on a live
# client plus a "seen vs gone" diff against the last sweep — the headless
# client used here exposes no event loop for that.
# FIXME(issue): register a MessageDeleted handler on the live client
# (``_build_headless_client`` consumer) that, on deletion of a message from a
# ``SHILL_WATCH_CHANNELS`` channel captured in a prior sweep, re-persists the
# already-captured screenshot/OCR with ``deleted=True`` so the site can show
# a "deleted posts" surface. Until that handler exists, deletion evidence is
# limited to the screenshots already captured before the delete.


async def capture_telegram_kol_posts(articles: list[Any], budget: int | None = None) -> int:
    """Capture screenshot + OCR for monitored Telegram KOL posts (bounded).

    For each swept article that carries a ``t.me/<channel>/<msg_id>`` source
    URL, captures the public ``t.me/s/`` web preview through the SAME
    pipeline as X captures (``tools.shill_shot.capture_and_ocr`` — Playwright
    + one DeepInfra VL-4B vision call) and persists the reference + OCR with
    ``source='telegram'``. Token/chain come from the article's extracted
    entities when present (empty otherwise — never fabricated). Bounded by
    ``budget`` (default ``TG_CAPTURE_BUDGET``). Fail-open: any capture/OCR
    error is logged and skipped. Returns the number captured.
    """
    if not TG_CAPTURE_ENABLED:
        return 0
    budget = TG_CAPTURE_BUDGET if budget is None else max(0, int(budget))
    if budget == 0:
        return 0
    try:
        from rugmunchbot.tools.shill_shot import capture_and_ocr
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_watch:shill_shot_import_failed err=%s", str(exc)[:120])
        return 0
    try:
        os.makedirs(SHILL_SCREENSHOT_DIR, exist_ok=True)
    except OSError as exc:  # fail-open — a read-only home must not crash the tick
        logger.debug("shill_watch:screenshot_dir_failed err=%s", str(exc)[:120])
        return 0
    captured = 0
    seen_urls: set[str] = set()
    for article in articles or []:
        if captured >= budget:
            break
        _, channel, url, entities = _article_fields(article)
        tg_url = _tg_post_url(url)
        if not tg_url or tg_url in seen_urls:
            continue
        seen_urls.add(tg_url)
        # Token identity from the canonical entity extraction when present.
        token = next((e for e in entities if e.startswith(("$", "0x")) or len(e) >= 32), "")
        chain = (
            "ticker" if token.startswith("$") else ("ethereum" if token.startswith("0x") else "")
        )
        out_path = os.path.join(
            SHILL_SCREENSHOT_DIR,
            f"tg_{(channel or 'unknown').replace('/', '_')[:32]}_{captured}_"
            f"{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.png",
        )
        shot = await capture_and_ocr(tg_url, out_path=out_path)
        if not shot.get("text"):
            continue
        shot["account"] = channel
        shot["source"] = "telegram"
        stub = ShillCampaign(
            token=token,
            chain=chain,
            accounts=[channel or "telegram"],
            mentions=[{"channel": channel, "title": "", "url": tg_url}],
        )
        await _persist_shill_evidence(stub, shot)
        captured += 1
    if captured:
        logger.info("shill_watch:tg_captured count=%s", captured)
    return captured


# ── Standalone evidence screenshot helpers (2026-09-04) ────────────────────
# Direct Playwright screenshot capture for a single tweet / Telegram message,
# saved under ``data/shill_evidence/{date}/{id}.png`` at the repo root. These
# are the "just the element" capture helpers the pipeline calls when a KOL is
# detected shilling a token. Fail-open: any Playwright/network/IO error returns
# None so the alert still posts on text-only evidence.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)
_EVIDENCE_ROOT = os.path.join(_REPO_ROOT, "data", "shill_evidence")
#: Per-capture timeout (15s per the spec) — a slow/blocked page never hangs
#: the tick.
_CAPTURE_TIMEOUT_MS = 15000

#: X status-id recognizer (same vocabulary as ``tools.read_tweet``).
_TWEET_ID_RE = re.compile(
    r"https?://(?:x\.com|twitter\.com)/(?:[^/]+|i)/status/(\d+)", re.IGNORECASE
)


def _tweet_id_from_url(url: str) -> str:
    """Numeric tweet id from an X status URL (or "")."""
    m = _TWEET_ID_RE.search(str(url or ""))
    return m.group(1) if m else ""


def _evidence_path(leaf: str) -> str:
    """``data/shill_evidence/{YYYYMMDD}/{leaf}.png`` (creates the dir, fail-open)."""
    date_dir = os.path.join(_EVIDENCE_ROOT, datetime.now(UTC).strftime("%Y%m%d"))
    try:
        os.makedirs(date_dir, exist_ok=True)
    except OSError as exc:  # fail-open — a read-only home must not crash the tick
        logger.debug("shill_watch:evidence_dir_failed err=%s", str(exc)[:120])
        return ""
    return os.path.join(date_dir, f"{leaf}.png")


def _file_ok(path: str) -> bool:
    """True when ``path`` exists and is non-empty (sync — avoids ASYNC240)."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


async def capture_tweet_screenshot(tweet_url: str) -> str | None:
    """Capture just the tweet element from ``tweet_url`` as PNG evidence.

    Saves to ``data/shill_evidence/{date}/{tweet_id}.png`` at the repo root.
    Uses Playwright (chromium, headless) to render the public status page and
    screenshot the tweet ``article`` element when it renders (falling back to
    the viewport so a login-wall still yields something). Timeout 15s.
    Fail-open: any error → None (the caller keeps text-only evidence).
    """
    tweet_id = _tweet_id_from_url(tweet_url)
    if not tweet_id:
        return None
    out_path = _evidence_path(tweet_id)
    if not out_path:
        return None
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_watch:playwright_import_failed err=%s", str(exc)[:120])
        return None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                await page.goto(
                    tweet_url, timeout=_CAPTURE_TIMEOUT_MS, wait_until="domcontentloaded"
                )
                el = await page.query_selector('article[data-testid="tweet"]')
                if el is not None:
                    await el.screenshot(path=out_path)
                else:
                    await page.screenshot(path=out_path, full_page=False)
            finally:
                await browser.close()
        if _file_ok(out_path):
            return out_path
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_watch:tweet_shot_failed err=%s", str(exc)[:120])
    return None


async def capture_telegram_message_screenshot(chat_id: str, message_id: str) -> str | None:
    """Capture a Telegram message's public web preview as PNG evidence.

    Renders ``https://t.me/s/{chat_id}/{message_id}`` (the same public preview
    the KOL capture uses) and screenshots the message element. Saves to
    ``data/shill_evidence/{date}/tg_{chat_id}_{message_id}.png``. Timeout 15s.
    Fail-open: any error → None.
    """
    chat = str(chat_id or "").strip().lstrip("@")
    msg = str(message_id or "").strip()
    if not chat or not msg:
        return None
    out_path = _evidence_path(f"tg_{chat}_{msg}")
    if not out_path:
        return None
    url = f"https://t.me/s/{chat}/{msg}"
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_watch:playwright_import_failed err=%s", str(exc)[:120])
        return None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                await page.goto(url, timeout=_CAPTURE_TIMEOUT_MS, wait_until="domcontentloaded")
                el = await page.query_selector(".tgme_widget_message")
                if el is not None:
                    await el.screenshot(path=out_path)
                else:
                    await page.screenshot(path=out_path, full_page=False)
            finally:
                await browser.close()
        if _file_ok(out_path):
            return out_path
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_watch:tg_shot_failed err=%s", str(exc)[:120])
    return None
