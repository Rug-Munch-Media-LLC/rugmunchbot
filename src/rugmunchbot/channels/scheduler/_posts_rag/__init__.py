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

Layout (modularized 2026-09-04 from the former single-module ``_posts_rag.py``):
  * ``__init__``  — shared config, mutable state, patchable imports + re-exports
  * ``_gates``    — quality / recency / source / news-content-type gates
  * ``_feed``     — feed.rugmunch.io posts.json + Ghost CMS mirror
  * ``_main``     — main gather → score → gate → post path
  * ``_fast_lane``— breaking-news fast lane + security-tweet escalation
"""

from __future__ import annotations

import html  # noqa: F401  (re-exported)
import json  # noqa: F401  (re-exported)
import os
import re
import time  # noqa: F401  (re-exported)
from datetime import UTC, datetime  # noqa: F401  (re-exported)
from typing import Any  # noqa: F401  (re-exported)

from rugmunchbot.channels.content import fetch_rag_drafts  # noqa: F401  (re-exported; tests patch)
from rugmunchbot.channels.formatters._primitives import (  # noqa: F401  (re-exported)
    NewsPost,
    enforce_site_link,
)
from rugmunchbot.channels.quality_schedule import (
    rerank_quality,  # noqa: F401  (re-exported; tests patch)
)
from rugmunchbot.channels.scheduler._core import (
    _chat_id as _chat_id,
    _post_photo_aware as _post_photo_aware,
    _release as _release,
    _remember as _remember,
    _safe_send as _safe_send,
    logger as logger,
)
from rugmunchbot.support.content_lint import BLAND_MIN_WORDS  # noqa: F401  (re-exported)

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

#: Content kinds that must NEVER land on @rmicryptonews (2026-09-03 channel
#: doctrine). The news channel is breaking-crypto-news ONLY — whale alerts,
#: scan results, system notifications, product updates, shill/alpha intel and
#: roundup/digest summaries are all routed to their own lanes. A draft whose
#: ``kind`` matches one of these is dropped from the news channel regardless
#: of how well it is written or how fresh it is. Unknown/empty kinds pass
#: (the rugbrain ``kind`` metadata is sparse today) — the recency/source/
#: quality gates still apply.
_NEWS_BLOCKED_KINDS: frozenset[str] = frozenset(
    {
        # whale alerts
        "whale_move",
        "whale_alert",
        "whale",
        # scan results
        "scan",
        "scanner_alert",
        "scan_result",
        # alerts / exploits / rugs / scammer warnings
        "alert",
        "exploit",
        "exploit_alert",
        "rug-update",
        "rug_update",
        "scammer_warning",
        "security_incident",
        # shill / alpha / kol intel
        "shill_evidence",
        "shill_watch",
        "shill_campaign",
        "shill",
        "kol_trade",
        "kol_shill",
        "early_entry",
        "alpha",
        # system / roundup / digest / market summaries
        "quality",
        "roundup",
        "digest",
        "market-summary",
        "market_summary",
        "system",
        "product_update",
        "changelog",
    }
)

# ── Ghost CMS mirror (feed.rugmunch.io, 2026-08-31) ─────────────────────────

#: Ghost Admin API base. Defaults to the canonical feed host; the Admin key
#: (``<id>:<secret>``) comes from the environment — never hardcoded.
GHOST_ADMIN_API_URL = os.getenv("GHOST_ADMIN_API_URL", "https://rugmunch.io")
GHOST_ADMIN_API_KEY = os.getenv("GHOST_ADMIN_API_KEY", "")

_ghost_no_key_logged = False

#: Consecutive main→feed mirror failures (W30 alerting state). Alert fires
#: once per failure streak; a success resets it (recovery logged once).
_mirror_fail_streak = 0

#: Fast-lane knobs (2026-08-26): critical+fresh intel must not wait for the
#: 45-min slow tick. The fast poster runs every RMI_RAG_FAST_LANE_MIN and
#: only escalates severity=="critical" drafts younger than 6h — everything
#: else stays on the slow lane. Shares the "rag:" dedupe namespace with the
#: slow loop, so a story can never post twice.
RAG_FAST_LANE_MIN = int(os.getenv("RMI_RAG_FAST_LANE_MIN", "10"))
RAG_FAST_LANE_MAX_AGE_H = float(os.getenv("RMI_RAG_FAST_LANE_MAX_AGE_H", "6"))

#: Security-tweet breaking-news escalation (2026-09-03): a tweet from a
#: monitored security account that mentions a security keyword AND has high
#: engagement (>50 likes or >20 retweets) posts to the main channel
#: (@cryptorugmuncher) IMMEDIATELY — it must not wait for the daily roundup.
#: The account list is the first-party security-intel set (the same accounts
#: the X-ingest lane watches); the keyword + engagement gates keep the main
#: channel to genuinely breaking security news, never routine noise.
SECURITY_TWEET_ACCOUNTS: frozenset[str] = frozenset(
    {
        "zachxbt",
        "peckshield",
        "blockaid",
        "certikalert",
        "slowmist",
        "beosin",
    }
)

#: Security keywords that mark a tweet as breaking security news (matched
#: case-insensitively against the tweet text).
SECURITY_TWEET_KEYWORDS: tuple[str, ...] = (
    "exploit",
    "hack",
    "drained",
    "rug",
    "scam",
    "flash loan",
    "bridge",
)

#: Engagement floor for escalation — "high engagement" = >50 likes OR >20
#: retweets (per the breaking-news spec).
SECURITY_TWEET_MIN_LIKES = 50
SECURITY_TWEET_MIN_RETWEETS = 20

#: Cadence of the security-tweet escalation job (minutes). 0 disables.
SECURITY_TWEET_ESCALATION_MIN = int(os.getenv("RMI_SECURITY_TWEET_ESCALATION_MIN", "5"))

# ══════════════════════════════════════════════════════════════════════════
# SUBMODULES — import after state/config so they can reference the package
# ══════════════════════════════════════════════════════════════════════════

from . import _fast_lane, _feed, _gates, _main  # noqa: E402,F401  (submodules re-exported)
from ._fast_lane import (  # noqa: E402
    _esc as _esc,
    _security_tweet_matches as _security_tweet_matches,
    _tweet_int as _tweet_int,
    format_security_tweet_escalation as format_security_tweet_escalation,
    post_rag_fast_lane as post_rag_fast_lane,
    post_security_tweet_escalation as post_security_tweet_escalation,
)
from ._feed import (  # noqa: E402
    _alert_mirror_failure as _alert_mirror_failure,
    _ghost_admin_jwt as _ghost_admin_jwt,
    _ghost_text_to_html as _ghost_text_to_html,
    _mirror_to_feed as _mirror_to_feed,
    _note_mirror_success as _note_mirror_success,
    _rag_post_with_image as _rag_post_with_image,
    ghost_mirror_post as ghost_mirror_post,
    mirror_post_to_feed as mirror_post_to_feed,
)
from ._gates import (  # noqa: E402
    _draft_age_hours as _draft_age_hours,
    _news_content_type_gate as _news_content_type_gate,
    _quality_gate as _quality_gate,
    _recency_gate as _recency_gate,
    _source_gate as _source_gate,
)
from ._main import (  # noqa: E402
    _fallback_main_drafts as _fallback_main_drafts,
    _regenerate_draft_text as _regenerate_draft_text,
    gather_main_drafts as gather_main_drafts,
    post_rag_drafts as post_rag_drafts,
)

__all__ = [
    "SECURITY_TWEET_ACCOUNTS",
    "SECURITY_TWEET_ESCALATION_MIN",
    "SECURITY_TWEET_KEYWORDS",
    "SECURITY_TWEET_MIN_LIKES",
    "SECURITY_TWEET_MIN_RETWEETS",
    "_alert_mirror_failure",
    "_fallback_main_drafts",
    "_note_mirror_success",
    "_quality_gate",
    "_recency_gate",
    "_source_gate",
    "format_security_tweet_escalation",
    "gather_main_drafts",
    "mirror_post_to_feed",
    "post_rag_drafts",
    "post_rag_fast_lane",
    "post_security_tweet_escalation",
]
