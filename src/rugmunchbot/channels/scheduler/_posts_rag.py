"""RAG-drafts posting — intelligence-ranked stories to all channels.

Pulls ready-to-post drafts from rugbrain's content-distribution API
(``channels/content.py::fetch_rag_drafts``) and routes them:

* alerts  → @rmialerts          (critical/high intel only)
* news    → @rmicryptonews      (solid news-value items)
* main    → @cryptorugmuncher   (top stories, full analysis) — every main
  post with ``mirror: true`` is ALSO written to the feed.rugmunch.io
  static site (same machine; atomic file append under /var/www).
* scans   → @rmiscans           (real scanner-feedback reports with ref
  links; wired 2026-08-26 — rugbrain generated these drafts but the bot
  never consumed the lane).

Dedup via ``_remember("rag", story_ref)`` — a story never posts twice.
Fail-open everywhere: RAG outage = zero posts, never a crash.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.content import fetch_rag_drafts
from rugmunchbot.channels.quality_schedule import rerank_quality

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

from rugmunchbot.channels.formatters._primitives import NewsPost, enforce_site_link
from rugmunchbot.channels.scheduler._core import (
    _chat_id,
    _post_photo_aware,
    _release,
    _remember,
    _safe_send,
    logger,
)
from rugmunchbot.support.content_lint import BLAND_MIN_WORDS

FEED_DIR = os.getenv("RMI_FEED_DIR", "/var/www/feed.rugmunch.io")
FEED_DATA = os.path.join(FEED_DIR, "data")
MAX_PER_CHANNEL_PER_TICK = int(os.getenv("RMI_RAG_POSTS_PER_TICK", "2"))

#: WO-3 quality gate (CONTENT-POSTING-SPEC.md): a draft whose rerank score
#: sits below this floor is DISCARDED — never posted anyway. Only drafts that
#: actually carry a positive score are gated; a missing/zero score means "no
#: signal" and passes (the rugbrain importance metadata is sparse today).
RMI_RAG_POST_MIN_SCORE = float(os.getenv("RMI_RAG_POST_MIN_SCORE", "0.30"))

#: Signature of the rugbrain ``_template_post`` fallback writer: "[SEV] Title"
#: first line + "Source: …" footer. Template output = DO NOT POST (spec law):
#: the LLM writer being down must degrade to silence, not to slop.
_TEMPLATE_SIGNATURE_RE = re.compile(
    r"^\[(?:CRITICAL|HIGH|MEDIUM|LOW)\]\s+.+\n\nSource:\s+", re.DOTALL
)

#: Second rugbrain template-writer variant (2026-08-30 feed incident): a
#: headline placeholder of exactly ``Story`` followed by a dash and a
#: ``Source:`` link — no real headline, no real writer. This shipped as a
#: "quality" post to @cryptorugmuncher + the feed mirror because the
#: ``_TEMPLATE_SIGNATURE_RE`` only needed ``[SEV] Title`` to match. Same
#: rule: template output must never post, silence beats slop.
_STORY_TEMPLATE_RE = re.compile(r"^Story\s*[—-]\s*Source:", re.IGNORECASE | re.DOTALL)

#: Placeholder source-link gate (2026-08-30 feed incident): the rugbrain
#: template writer emits numbered reference URLs (``/ref-1``, ``/ref-2``)
#: on reserved demo hosts. A real sourced story never links ``/ref-N``. This
#: catches the template variant regardless of its prefix, without flagging
#: ordinary ``example.com/…`` fixture links (different path).
_PLACEHOLDER_SOURCE_RE = re.compile(
    r"https?://(?:example\.(?:com|org|net)|[^\s/]*placeholder[^\s/]*)/ref-\d+",
    re.IGNORECASE,
)

#: Recency gate (2026-08-26 stale-news incident): a draft older than this is
#: DISCARDED, and an undated draft is discarded too — we proved undated
#: drafts are how 2022-2024 exploits reached main. Strategy v2: silence
#: beats stale. Override with RMI_RAG_POST_MAX_AGE_H (0 disables).
RAG_POST_MAX_AGE_H = float(os.getenv("RMI_RAG_POST_MAX_AGE_H", "72"))

#: Source-link gate (2026-08-26 no-source incident): a draft whose text
#: carries NO http(s) link is DISCARDED — "Source: (none provided)" reached
#: @rmialerts because the writer faithfully echoed a missing URL. Defense in
#: depth behind rugbrain's selection-side url requirement. Scan drafts embed
#: their ref_link in the text, so they pass naturally.
_SOURCE_LINK_RE = re.compile(r"https?://\S+", re.IGNORECASE)

#: Embedded-date probe: catches stale text even when ``ts`` lies or is
#: missing ("… exploit on September 16, 2024 …" → 2024 → reject).
_EMBEDDED_DATE_RE = re.compile(
    r"\b(20\d{2})-(\d{2})-(\d{2})"
    r"|\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),?\s+(20\d{2})\b",
    re.IGNORECASE,
)

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}


def _draft_age_hours(item: dict[str, Any]) -> tuple[float | None, str]:
    """Best-effort age of a draft in hours + which probe matched.

    Order: explicit ``ts``/``published_at`` field → first date embedded in
    the text. Returns ``(None, "none")`` when no date could be found.
    """
    raw = str(item.get("ts") or item.get("published_at") or "").strip()
    if raw:
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            return max((datetime.now(UTC) - ts).total_seconds() / 3600.0, 0.0), "field"
        except ValueError:
            pass
    m = _EMBEDDED_DATE_RE.search(str(item.get("text") or ""))
    if m:
        try:
            if m.group(1):  # ISO form 2024-09-16
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:  # "September 16, 2024"
                y = int(m.group(6))
                mo = _MONTHS.get((m.group(4) or "").lower(), 0)
                d = int(m.group(5))
            if mo and d:
                ts = datetime(y, mo, d, tzinfo=UTC)
                return max((datetime.now(UTC) - ts).total_seconds() / 3600.0, 0.0), "text"
        except (ValueError, TypeError):
            return None, "none"
    return None, "none"


def _recency_gate(item: dict[str, Any]) -> str:
    """Stale-news gate. Returns "" when the draft may post, else the reason.

    A draft fails when: it carries no discoverable date at all (undated =
    unverifiable freshness), or its age exceeds RAG_POST_MAX_AGE_H. Pure —
    never raises; unparsable dates count as undated.
    """
    if RAG_POST_MAX_AGE_H <= 0:
        return ""
    age_h, probe = _draft_age_hours(item)
    if age_h is None:
        return "undated"
    if age_h > RAG_POST_MAX_AGE_H:
        return f"stale({age_h:.0f}h via {probe})"
    return ""


def _source_gate(item: dict[str, Any]) -> str:
    """No-source gate. Returns "" when the draft may post, else the reason.

    A draft fails when its text contains no http(s) link at all — an
    unsourced intelligence post is unverifiable and must not publish
    (silence beats slop). Pure — never raises.
    """
    if _SOURCE_LINK_RE.search(str(item.get("text") or "")):
        return ""
    return "no_source_link"


def _quality_gate(item: dict[str, Any]) -> str:
    """WO-3 draft gate. Returns "" when the draft may post, else the reason.

    Checks (in order): explicit ``writer == "template"`` marker (rugbrain
    2026-08-25), the template signature heuristic (covers containers built
    before the marker shipped), and the rerank-score floor. Pure — never
    raises; unparsable scores pass.

    2026-08-30 unify (task 2): the importance floor now reads ``importance``
    too, not just ``score``/``rerank_score``. Rugbrain pre-scores its drafts
    into ``score``/``rerank_score``; the bot's own reranker
    (``quality_schedule.rerank_quality`` — used on the fallback lane) writes
    ``importance``. Checking all three means the 0.30 floor applies uniformly
    — a reranked fallback headline scoring 0.01-0.12 can no longer slip
    through just because it had no ``score`` field (the old unscored bypass).
    """
    writer = str(item.get("writer") or "").strip().lower()
    if writer == "template":
        return "template_writer"
    text = str(item.get("text") or "")
    # Template-fallback output has many shapes: "[SEV] Title\n\nSource:" AND
    # the "Story — Source:" headline-placeholder variant. Both are the LLM
    # writer being down — neither ever posts (silence beats slop). The
    # placeholder ref-URL gate is applied regardless of the ``writer`` label —
    # a template that slipped through unlabeled still must not publish.
    if _TEMPLATE_SIGNATURE_RE.match(text.strip()) or _STORY_TEMPLATE_RE.match(text.strip()):
        return "template_signature"
    if _PLACEHOLDER_SOURCE_RE.search(text):
        return "placeholder_source"
    # Confirmed-score keys: rugbrain ("score"/"rerank_score") + bot reranker
    # ("importance"). Any explicit score under the floor is DISCARDED; a
    # missing/unparsable score means "no signal" and passes (the rugbrain
    # importance metadata is sparse today) — but once WE rerank a fallback
    # draft it carries a real ``importance``, so it never slips unscored.
    for key in ("score", "rerank_score", "importance"):
        raw_score = item.get(key)
        if raw_score is None:
            continue
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            return ""  # unparsable → no usable signal → pass
        if score > 0 and score < RMI_RAG_POST_MIN_SCORE:
            return f"below_threshold({score:.2f})"
    return ""


def mirror_post_to_feed(item: dict[str, Any]) -> str | None:
    """Append a post to the feed site's JSON store. Returns slug (None on fail).

    Public entry point (2026-08-29): the exploit-alert poster
    (``channels.exploit_alerts``) reuses this exact writer so every
    main-channel post — RAG story or exploit alert — mirrors to
    feed.rugmunch.io through the same atomic file path. ``_mirror_to_feed``
    remains as a backward-compatible alias for ``_posts_rag`` callers.
    """
    try:
        os.makedirs(FEED_DATA, exist_ok=True)
        path = os.path.join(FEED_DATA, "posts.json")
        posts: list[dict[str, Any]] = []
        if os.path.exists(path):
            with open(path) as f:
                try:
                    loaded = json.load(f)
                except json.JSONDecodeError:
                    loaded = None
            # Tolerant read: the deployed store was seeded as a dict wrapper
            # ({"posts": []}) — the canonical shape is a top-level array.
            if isinstance(loaded, dict):
                posts = list(loaded.get("posts") or [])
            elif isinstance(loaded, list):
                posts = list(loaded)
        slug = f"post-{int(time.time())}"
        # Thumbnail (2026-09): carry the story image (og:image / evidence
        # screenshot) into the static feed store so the SPA can render it.
        # http(s)-only — a server-side path is useless to the browser.
        _image = str(item.get("image_url") or item.get("image") or "").strip()
        if not _image.startswith(("http://", "https://")):
            _image = ""
        # Dedup guard (stable-key): a re-post of the same story_ref/source
        # into both surfaces must not double-post on the feed. The Telegram
        # lane already dedupes its sends; this keeps the static feed store
        # idempotent as a second line of defense.
        _ref = str(item.get("story_ref") or item.get("source") or "")
        _text = str(item.get("text", ""))
        _existing = next(
            (p for p in posts if p.get("source") == _ref and p.get("text") == _text),
            None,
        )
        if _existing is not None:
            return str(_existing.get("slug") or slug)
        _payload = {
            "slug": slug,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "text": _text,
            "source": _ref,
            "kind": item.get("kind", "story"),
        }
        if _image:
            _payload["image"] = _image
        posts.insert(0, _payload)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(posts[:500], f, indent=1)
        os.replace(tmp, path)
        # Ghost CMS mirror (2026-08-31): posts.json write succeeded — now
        # push to Ghost (canonical store for feed.rugmunch.io). Fail-open:
        # a Ghost outage returns None and the local slug is used.
        try:
            ghost_slug = ghost_mirror_post(item, slug)
        except Exception as ghost_err:  # belt-and-braces: never lose the slug
            logger.warning("ghost_mirror_unexpected err=%s", str(ghost_err)[:200])
            ghost_slug = None
        return ghost_slug or slug
    except Exception as e:
        # W30: mirror is MANDATORY (CONTENT-STRATEGY-CANONICAL v2) — a
        # failure must be visible, not a debug whisper. The operator alert
        # itself fires from post_rag_drafts (needs the bot handle); here we
        # only escalate the log level.
        logger.warning("feed_mirror_failed err=%s", str(e)[:200])
        return None


_mirror_to_feed = mirror_post_to_feed  # backward-compatible alias


# ── Ghost CMS mirror (feed.rugmunch.io, 2026-08-31) ─────────────────────────

#: Ghost Admin API base. Defaults to the canonical feed host; the Admin key
#: (``<id>:<secret>``) comes from the environment — never hardcoded.
GHOST_ADMIN_API_URL = os.getenv("GHOST_ADMIN_API_URL", "https://feed.rugmunch.io")
GHOST_ADMIN_API_KEY = os.getenv("GHOST_ADMIN_API_KEY", "")

_ghost_no_key_logged = False


def _ghost_admin_jwt(api_key: str) -> str:
    """Sign a short-lived Ghost Admin API JWT (HS256, aud=/admin/)."""
    import base64
    import hashlib
    import hmac

    parts = api_key.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("GHOST_ADMIN_API_KEY must be <id>:<secret>")
    key_id, secret = parts
    iat = int(time.time())

    def _b64url(data: bytes) -> str:

        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header = _b64url(
        json.dumps({"alg": "HS256", "typ": "JWT", "kid": key_id}, separators=(",", ":")).encode()
    )
    payload = _b64url(
        json.dumps({"iat": iat, "exp": iat + 300, "aud": "/admin/"}, separators=(",", ":")).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = _b64url(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def _ghost_text_to_html(text: str) -> str:
    """Plain text → minimal safe HTML paragraphs (escape everything)."""
    import html as _html

    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "".join(f"<p>{_html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras) or "<p></p>"


def ghost_mirror_post(item: dict[str, Any], slug: str) -> str | None:
    """Push one mirrored post to Ghost via the Admin API (fail-open).

    Called from :func:`mirror_post_to_feed` after the posts.json write
    succeeds. Ghost is the canonical store for feed.rugmunch.io going
    forward; posts.json stays as the fallback payload. Returns the Ghost
    slug (``/p/{slug}`` link) or None on any failure — posting to Telegram
    is never blocked by the Ghost mirror.
    """
    global _ghost_no_key_logged
    if not GHOST_ADMIN_API_KEY:
        if not _ghost_no_key_logged:
            _ghost_no_key_logged = True
            logger.info("ghost_mirror_skipped_no_admin_key")
        return None
    try:

        import httpx

        token = _ghost_admin_jwt(GHOST_ADMIN_API_KEY)
        _image = str(item.get("image_url") or item.get("image") or "").strip()
        payload = {
            "posts": [
                {
                    "title": str(item.get("title") or item.get("text", "")[:80] or slug),
                    "html": _ghost_text_to_html(str(item.get("text", ""))),
                    "status": "published",
                    "visibility": "public",
                    "slug": slug,
                    "tags": [{"name": str(item.get("kind", "story"))}],
                    **(
                        {"feature_image": _image}
                        if _image.startswith(("http://", "https://"))
                        else {}
                    ),
                    **(
                        {"custom_excerpt": str(item.get("text", ""))[:300]}
                        if item.get("text")
                        else {}
                    ),
                }
            ]
        }
        resp = httpx.post(
            f"{GHOST_ADMIN_API_URL.rstrip('/')}/ghost/api/admin/posts/",
            headers={
                "Authorization": f"Ghost {token}",
                "Content-Type": "application/json",
                "Accept-Version": "v5.0",
            },
            json=payload,
            timeout=8.0,
        )
        if resp.status_code in (200, 201):
            post = resp.json().get("posts", [{}])[0]
            ghost_slug = str(post.get("slug") or slug)
            logger.info("ghost_mirror_published slug=%s", ghost_slug)
            return ghost_slug
        logger.warning(
            "ghost_mirror_http_error status=%s body=%s", resp.status_code, resp.text[:200]
        )
        return None
    except Exception as exc:  # fail-open: Ghost outage must never break posting
        logger.warning("ghost_mirror_failed err=%s", str(exc)[:200])
        return None


def _rag_post_with_image(text: str, item: dict[str, Any]) -> NewsPost | str:
    """Wrap a RAG draft's text as a ``NewsPost`` carrying its image (if any).

    Main-channel doctrine (2026-08-29): a flagship post attaches the story's
    image when the draft provides one — the photo-aware sender
    (``_post_photo_aware``) then posts photo + caption, falling back to text.
    Fail-open: no usable image → the plain text string (unchanged behavior).
    """
    image_url = str(item.get("image_url") or item.get("image") or "").strip()
    if not (image_url.startswith(("http://", "https://"))):
        return text
    post = NewsPost(text)
    post.image_url = image_url
    return post


#: Consecutive main→feed mirror failures (W30 alerting state). Alert fires
#: once per failure streak; a success resets it (recovery logged once).
_mirror_fail_streak = 0


async def _alert_mirror_failure(bot: Any, err_hint: str) -> None:
    """Notify the operator channel that the mandatory feed mirror broke.

    CONTENT-STRATEGY-CANONICAL v2 / W30: every main post MUST land on
    feed.rugmunch.io; a silent mirror gap breaks the operator-social
    contract. Alerts fire to the ``updates`` role (@rmiupdates) once per
    failure streak — never per tick. Fail-open: any error here is logged
    and swallowed; alerting must never break posting.
    """
    global _mirror_fail_streak
    _mirror_fail_streak += 1
    if _mirror_fail_streak > 1:
        return  # already alerted for this streak
    logger.warning("feed_mirror_down streak_started=%d hint=%s", _mirror_fail_streak, err_hint[:80])
    try:
        from rugmunchbot.config import _resolve_channel_id

        ops_chat = _resolve_channel_id("updates")
        if not ops_chat:
            return
        await bot.send_message(
            ops_chat,
            "\U0001f6a8 <b>Feed mirror DOWN</b>\n"
            "Main-channel posts are NOT reaching feed.rugmunch.io "
            "(mandatory mirror, strategy v2/W30).\n"
            f"hint: <code>{err_hint[:120]}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("feed_mirror_alert_failed err=%s", str(e)[:120])


def _note_mirror_success() -> None:
    """Reset the W30 failure streak; log recovery exactly once."""
    global _mirror_fail_streak
    if _mirror_fail_streak:
        logger.info("feed_mirror_recovered after=%d failures", _mirror_fail_streak)
    _mirror_fail_streak = 0


async def _fallback_main_drafts(limit: int = 6) -> list[dict[str, Any]]:
    """Bot-owned content candidates for the main lane (fail-open → []).

    Uses our own ``fetch_trending_news`` (real backend headlines with writer
    + source URL) so the main lane posts even when rugbrain's
    content-distribution lane is down, disabled, or silent. Each item is shaped
    into a draft the lane gates accept (a ``ts`` so the recency gate passes,
    an http(s) link in the text so the source gate passes), and carries a
    stable ``story_ref`` so the global dedupe can never double-post it.

    2026-08-30 unify: moved here from ``_posts_daily`` so it is a SINGLE
    source of truth for the folded-in daily-quality fallback shared by BOTH
    the channel_rag lane and the daily_quality job.
    """
    try:
        from rugmunchbot.channels.content import fetch_trending_news

        items = await fetch_trending_news(limit=limit)
    except Exception as exc:  # fail-open by contract
        logger.debug("main_gather:fallback_fetch_failed err=%s", str(exc)[:120])
        return []
    now = datetime.now(UTC).isoformat()
    drafts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in items:
        url = str(it.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        text = f"{it.get('title') or it.get('summary') or ''} {url}".strip()
        if not text:
            continue
        drafts.append(
            {
                "story_ref": url,
                "title": str(it.get("title") or url)[:120],
                "text": text,
                "source": str(it.get("source") or "rugmunch")[:80],
                "ts": now,
                "kind": "quality",
                "url": url,
            }
        )
    return drafts


async def _regenerate_draft_text(text: str, violations: list[Any]) -> str:
    """Stricter-prompt LLM rewrite for a draft that failed the quality gate.

    Used as the gate's ONE regeneration retry on the prose lanes (main/news):
    a headline+link stub is rewritten into a real ≥20-word commentary block.
    Fail-open: any error returns "" (the gate then blocks the post).
    """
    from rugmunchbot.llm_config import chat_with_fallback

    detail = "; ".join(f"{v.rule}: {v.phrase}" for v in violations[:5])
    url_match = re.search(r"https?://\S+", text or "")
    url = url_match.group(0) if url_match else ""
    title = re.sub(r"https?://\S+", "", text or "").strip()[:200]
    messages = [
        {
            "role": "system",
            "content": (
                "You write Telegram channel posts for a crypto intelligence "
                "desk. Rules: at least 20 words of original commentary, no "
                "banned slop phrases (NFA, DYOR, HODL, 'stay safe', 'delve "
                "into', 'ever-evolving'), the source URL appears EXACTLY "
                "once, no '[SEV] Title / Source:' template shape. Output "
                "ONLY the post text."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Headline: {title}\nSource URL: {url}\n"
                f"Lint failures to fix: {detail[:300]}\n\n"
                "Write the full post now (commentary + the source link once)."
            ),
        },
    ]
    return (await chat_with_fallback(messages, "snappy")).strip()


async def gather_main_drafts(limit: int = 8) -> list[dict[str, Any]]:
    """Single unified main-content gather: RAG primary → scored fallback → gate.

    2026-08-30 unify (task 1): the ``channel_rag`` lane and the ``daily_quality``
    job now share ONE gather→generate→score→gate→post path instead of two
    divergent lanes. This is that shared gather:

    * **Primary** — channel_rag's drafts (rugbrain ``/content/drafts`` on the
      ``main`` channel) via ``fetch_rag_drafts``.
    * **Fallback** — when the RAG lane returns nothing (writer down / lagging /
      disabled), we fold in the bot's own curated headlines.
    * **Score** — the fallback is routed through ``rerank_quality`` so every
      fallback draft *carries* a real ``importance`` (task 2: no unscored
      bypass).
    * **Gate** — each surviving candidate is checked by the unified gates
      (quality / recency / source), which now apply the 0.30 importance floor
      uniformly to scored drafts.

    Returns drafts already scored + past every main gate. Fail-open → [] on
    any error (silence beats slop, never a crash).
    """
    try:
        primary = await fetch_rag_drafts("main", limit=max(1, limit))
    except Exception as exc:  # fail-open by contract
        logger.warning("main_gather:rag_fetch_failed channel=main err=%s", str(exc)[:120])
        primary = []
    if primary:
        drafts = [dict(d) for d in primary]
    else:
        # 2026-08-30 unify (task 3): a silent RAG lane must be VISIBLE — WARN
        # and fall back into the scored bot-sourced candidates, never no-op.
        logger.warning("main_gather:rag_lane_empty channel=main falling_back_to_bot_sources")
        drafts = await _fallback_main_drafts(limit)
        if drafts:
            drafts = await rerank_quality(drafts)
    kept: list[dict[str, Any]] = []
    for item in drafts:
        gate = _quality_gate(item) or _recency_gate(item) or _source_gate(item)
        if gate:
            logger.info(
                "main_gather:gated channel=main reason=%s ref=%s",
                gate,
                str(item.get("story_ref") or "")[:60],
            )
            continue
        kept.append(item)
    return kept


async def post_rag_drafts(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Route RAG-ranked drafts to their channels. Returns posted count.

    2026-08-25 P0 fix — ordering: the dedupe mark (``_remember``) used to be
    taken BEFORE the chat-id check and BEFORE the send, so any draft that hit
    a missing chat id or a single failed send was permanently burned (marked
    seen, never posted, never retried — a silent blackhole). Now: chat id is
    resolved first, the send must succeed, and only then is the story marked.
    Cost: a send that fails in two overlapping ticks could double-post; at a
    45-min interval with in-tick sequencing that risk is negligible next to
    losing drafts forever.
    """
    posted_total = 0

    # main FIRST: when rugbrain queues the same story for several channels,
    # the global dedupe (_remember strips one lane prefix) keeps only the
    # first lane to claim it — the flagship channel + feed mirror must win.
    # 2026-08-30 unify: the main channel sources through the SINGLE
    # gather→score→gate path (``gather_main_drafts``) shared with
    # channel_daily_quality — so RAG-empty WARNs and folds in the scored
    # fallback instead of silently posting zero.
    for channel in ("main", "news", "alerts", "scans"):
        if channel == "main":
            drafts = await gather_main_drafts(limit=MAX_PER_CHANNEL_PER_TICK + 2)
        else:
            drafts = await fetch_rag_drafts(channel, limit=MAX_PER_CHANNEL_PER_TICK + 2)
        chat_id = _chat_id(channel)
        if not chat_id:
            logger.debug("rag_drafts_skipped channel=%s reason=no_chat_id", channel)
            continue
        sent_this_channel = 0
        for item in drafts:
            if sent_this_channel >= MAX_PER_CHANNEL_PER_TICK:
                break
            # WO-3 quality gate BEFORE the dedupe mark: a gated draft must
            # stay retryable (the writer may be healthy next cycle), so it
            # is skipped without burning its story_ref.
            gate_reason = _quality_gate(item)
            if not gate_reason:
                # 2026-08-26 stale-news incident: quality alone is not
                # freshness — a well-written 2022 exploit still must not
                # post. Undated drafts are rejected too (fail-closed).
                gate_reason = _recency_gate(item)
            if not gate_reason:
                # 2026-08-26 no-source incident: an unsourced post is
                # unverifiable — rejected regardless of how well written.
                gate_reason = _source_gate(item)
            if gate_reason:
                logger.warning(
                    "rag_draft_gated channel=%s reason=%s ref=%s",
                    channel,
                    gate_reason,
                    str(item.get("story_ref") or "")[:60],
                )
                continue
            ref = item.get("story_ref") or item.get("text", "")[:60]
            # SINGLE lane prefix ("rag:") — _remember's global namespace
            # strips exactly one "x:" segment, so "rag:<ref>" dedupes the
            # bare <ref> across channels. The old "rag:<channel>:<ref>" left
            # the channel inside the bare key → cross-channel dedupe never
            # fired and one story could post to every channel.
            dedupe_key = f"rag:{ref}"
            if not _remember(f"rag_{channel}", dedupe_key):
                continue

            # Site-link contract (2026-08-29): main posts carry the exact
            # feed.rugmunch.io link (the mirror they land on), news posts the
            # rugmunch.io/news link — never a linkless flagship post.
            text = str(item.get("text", ""))
            if channel in ("main", "news"):
                text = enforce_site_link(text, site="feed" if channel == "main" else "news")

            # HARD quality gate (2026-08-31): lint → autofix → skip. A draft
            # that still fails after autofix does not ship and stays
            # retryable (dedupe mark not yet burned — the mark below is
            # taken only after a successful send).
            # Image doctrine (2026-08-31): EVERY channel post carries an
            # image. Resolve before the gate — carried image → og:image from
            # the source URL (news lanes) → branded placeholder — so the
            # gate's require_image check can never block on a missing image
            # and _post_photo_aware always attaches a photo.
            from rugmunchbot.support.image_policy import (
                ensure_post_image,
                resolve_post_image,
            )
            from rugmunchbot.support.quality_gate import pre_post_quality_gate

            resolved_image = await resolve_post_image(
                channel,
                image_url=item.get("image_url") or item.get("image") or "",
                source_url=item.get("url") or item.get("source_url") or "",
                logger=logger,
            )
            gate = await pre_post_quality_gate(
                text,
                image_url=resolved_image,
                source=str(item.get("story_ref") or "")[:80],
                kind=f"rag:{channel}",
                # Structured scan/alert formats are terse by design; only
                # prose lanes (main/news) enforce the full 20-word floor.
                # Image doctrine is ALL channels: every post ships a photo
                # (carried, og:image, or branded placeholder).
                min_words=BLAND_MIN_WORDS if channel in ("main", "news") else 8,
                require_image=True,
                allow_placeholder_image=True,
                regenerate=_regenerate_draft_text if channel in ("main", "news") else None,
                logger=logger,
            )
            if not gate.ok:
                continue
            text = gate.text
            item = {**item, "image_url": gate.image_url}

            ok = await _post_photo_aware(
                ctx.bot,
                chat_id,
                ensure_post_image(_rag_post_with_image(text, item)),
                _safe_send,
            )
            if not ok:
                # Release the dedupe mark so the next tick retries this draft
                # instead of black-holing it (see docstring).
                _release(f"rag_{channel}", dedupe_key)
                continue

            sent_this_channel += 1
            posted_total += 1

            if channel == "main" and item.get("mirror"):
                slug = _mirror_to_feed(item)
                if slug:
                    _note_mirror_success()
                    logger.info("rag_post_mirrored_to_feed slug=%s", slug)
                else:
                    # W30: mandatory mirror failed — operator must know.
                    await _alert_mirror_failure(ctx.bot, str(item.get("story_ref") or "")[:120])

    if posted_total:
        logger.info("rag_drafts_posted count=%d", posted_total)
    return posted_total


#: Fast-lane knobs (2026-08-26): critical+fresh intel must not wait for the
#: 45-min slow tick. The fast poster runs every RMI_RAG_FAST_LANE_MIN and
#: only escalates severity=="critical" drafts younger than 6h — everything
#: else stays on the slow lane. Shares the "rag:" dedupe namespace with the
#: slow loop, so a story can never post twice.
RAG_FAST_LANE_MIN = int(os.getenv("RMI_RAG_FAST_LANE_MIN", "10"))
RAG_FAST_LANE_MAX_AGE_H = float(os.getenv("RMI_RAG_FAST_LANE_MAX_AGE_H", "6"))


async def post_rag_fast_lane(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Breaking-news fast lane → @rmialerts, critical+fresh only.

    Returns posted count (0 or 1). All slow-lane gates apply (quality,
    recency, source) PLUS severity=="critical" and a tighter age cap. The
    shared dedupe key means the slow tick skips anything this already
    delivered; a failed send releases the mark for either lane to retry.
    """
    chat_id = _chat_id("alerts")
    if not chat_id:
        return 0
    drafts = await fetch_rag_drafts("alerts", limit=3)
    for item in drafts:
        if str(item.get("severity") or "").lower() != "critical":
            continue
        gate_reason = _quality_gate(item)
        if not gate_reason:
            gate_reason = _recency_gate(item)
        if not gate_reason:
            gate_reason = _source_gate(item)
        age_h, probe = _draft_age_hours(item)
        if not gate_reason and age_h is not None and age_h > RAG_FAST_LANE_MAX_AGE_H:
            gate_reason = f"fast_lane_stale({age_h:.0f}h via {probe})"
        if gate_reason:
            logger.debug(
                "rag_fast_lane_skipped reason=%s ref=%s",
                gate_reason,
                str(item.get("story_ref") or "")[:60],
            )
            continue
        dedupe_key = f"rag:{item.get('story_ref') or item.get('text', '')[:60]}"
        if not _remember("rag_alerts", dedupe_key):
            return 0  # slow lane already claimed it
        # HARD quality gate (2026-08-31) — same contract as the slow lane,
        # including the all-channel image doctrine (resolve → gate → photo).
        from rugmunchbot.support.image_policy import ensure_post_image, resolve_post_image
        from rugmunchbot.support.quality_gate import pre_post_quality_gate

        resolved_image = await resolve_post_image(
            "alerts",
            image_url=item.get("image_url") or item.get("image") or "",
            source_url=item.get("url") or item.get("source_url") or "",
            logger=logger,
        )
        gate = await pre_post_quality_gate(
            str(item.get("text", "")),
            image_url=resolved_image,
            source=str(item.get("story_ref") or "")[:80],
            kind="rag:fast_lane",
            logger=logger,
        )
        if not gate.ok:
            _release("rag_alerts", dedupe_key)
            continue
        # Photo-aware send (2026-08-31): the fast lane is a channel post too —
        # it must never ship as a naked text message.
        if await _post_photo_aware(
            ctx.bot,
            chat_id,
            ensure_post_image(
                _rag_post_with_image(gate.text, {**item, "image_url": gate.image_url})
            ),
            _safe_send,
        ):
            logger.info(
                "rag_fast_lane_posted ref=%s age_h=%.1f",
                str(item.get("story_ref") or "")[:60],
                age_h if age_h is not None else -1.0,
            )
            return 1
        _release("rag_alerts", dedupe_key)
        return 0
    return 0


__all__ = [
    "_alert_mirror_failure",
    "_fallback_main_drafts",
    "_note_mirror_success",
    "_quality_gate",
    "_recency_gate",
    "_source_gate",
    "gather_main_drafts",
    "mirror_post_to_feed",
    "post_rag_drafts",
    "post_rag_fast_lane",
]
