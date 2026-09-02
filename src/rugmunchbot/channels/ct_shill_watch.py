"""CT timeline shill watch — ingest the X timeline of influential CT accounts
via ApiPass, catch shills in the act, screenshot the evidence, and grow the
Wall of Shame continuously.

Operator mandate (2026-09-02): "make full use of our apipass.dev twitter
privileges … ingest as much of the ct timeline as possible, catch shills in
the act on TG + X, screenshot, add to the KOL list, continuously improved
social intelligence setup."

What this module adds on top of the existing rails
--------------------------------------------------
* ``channels.shill_watch`` watches TELEGRAM call channels for coordinated
  campaigns. ``channels.kol_discovery`` searches X for shilled tokens.
  Neither ingests the CT TIMELINE itself. This module rotates through the
  curated ``influential_accounts.json`` registry (~500 handles) on a 15-min
  tick, pulling each account's recent tweets via ``tools.apipass_x``.
* Every tweet that promotes a token ($CASHTAG / address) with real
  engagement is a shill candidate. Confirmation is scanner-backed
  (``kol_discovery._is_risky_token`` → honeypot/rug/LP evidence) OR a
  Telegram cross-hit (the same handle already has TG shill evidence in the
  backend store → "coordinated shill", higher severity).
* Confirmed shills get screenshot evidence (``tools.shill_shot`` — survives
  tweet deletion), are auto-added/updated in ``data/kol/wall_of_shame.json``
  (shill_count / tokens_shilled / last_shill_ts / platforms), mirrored to
  the backend evidence store, and embedded into the Qdrant ``social_intel``
  collection so RAG can answer "who shilled $X" / "shills of tokens that
  rugged".
* A daily refresh job rescores every Wall of Shame entry (shill severity =
  tokens shilled x rugged fraction x coordination) and discovers NEW KOLs
  from the retweeters of known shill tweets (one governed ApiPass pull).

Budget doctrine: the ``apipass_x`` governor (≤ ``RMI_APIPASS_MAX_CALLS_PER_HOUR``
task creations) is the hard ceiling. Each account timeline costs 2 creates
(handle→id resolve + timeline), so the per-tick account count is clamped to
the remaining budget. Fail-open everywhere: no key, dead scanner, dead
Qdrant, read-only data dir → zero catches, never raises.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.ct_shill_watch")

# ══════════════════════════════════════════════════════════════════════════
# CONFIG (env-overridable)
# ══════════════════════════════════════════════════════════════════════════

CT_SHILL_ENABLED = os.getenv("RMI_CT_SHILL_ENABLED", "true").lower() in ("1", "true", "yes")
#: Tick interval (minutes). The operator asked for 15.
CT_SHILL_INTERVAL_MIN = int(os.getenv("RMI_CT_SHILL_INTERVAL_MIN", "15"))
#: Accounts pulled per tick. Each costs 2 ApiPass creates (resolve + timeline);
#: the governor inside apipass_x is the hard ceiling — we never exceed
#: ``task_creates_remaining()``.
CT_SHILL_ACCOUNTS_PER_TICK = int(os.getenv("RMI_CT_SHILL_ACCOUNTS_PER_TICK", "2"))
#: Tweets inspected per account timeline.
CT_SHILL_TWEETS_PER_ACCOUNT = int(os.getenv("RMI_CT_SHILL_TWEETS_PER_ACCOUNT", "10"))
#: Engagement floor (likes + retweets) for a promo tweet to count as reach.
CT_SHILL_MIN_ENGAGEMENT = int(os.getenv("RMI_CT_SHILL_MIN_ENGAGEMENT", "25"))
#: Max screenshots captured per tick (one DeepInfra vision call each).
CT_SHILL_SCREENSHOT_BUDGET = int(os.getenv("RMI_CT_SHILL_SCREENSHOT_BUDGET", "3"))
#: Max wall-of-shame writes per tick (bounded, no list spam).
CT_SHILL_MAX_ADDS_PER_TICK = int(os.getenv("RMI_CT_SHILL_MAX_ADDS", "3"))

#: Daily refresh knobs.
KOL_DAILY_REFRESH_ENABLED = os.getenv("RMI_KOL_DAILY_REFRESH_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
KOL_DAILY_INTERVAL_MIN = int(os.getenv("RMI_KOL_DAILY_INTERVAL_MIN", "1440"))
#: Retweeter-discovery pulls per daily run (each is one ApiPass createTask).
KOL_DAILY_MAX_RETWEETER_PULLS = int(os.getenv("RMI_KOL_DAILY_MAX_RETWEETER_PULLS", "1"))

#: Canonical Qdrant endpoint + social-intel collection for shill records
#: (same direct-upsert pattern as ``shill_watch`` → ``threat_intel_v2``).
_QDRANT_URL = os.getenv("RMI_QDRANT_URL") or os.getenv(
    "QDRANT_URL", os.getenv("QDRANT_FALLBACK", "http://rmi-qdrant-v2:6333")
).rstrip("/")
SOCIAL_INTEL_COLLECTION = os.getenv("RMI_SOCIAL_INTEL_COLLECTION", "social_intel")

#: Data paths (bundled kol dir; env-overridable for backend handoff).
_KOL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "kol")
INFLUENTIAL_ACCOUNTS_PATH = os.getenv(
    "RMI_INFLUENTIAL_ACCOUNTS_PATH", os.path.join(_KOL_DIR, "influential_accounts.json")
)
WALL_OF_SHAME_PATH = os.getenv(
    "RMI_WALL_OF_SHAME_PATH", os.path.join(_KOL_DIR, "wall_of_shame.json")
)
#: Rotation cursor so each 15-min tick pulls the NEXT slice of the CT
#: registry (no account is starved; the whole registry cycles).
_CURSOR_PATH = os.getenv("RMI_CT_SHILL_CURSOR_PATH", os.path.join(_KOL_DIR, "ct_cursor.json"))

_BLUECHIP = {"BTC", "ETH", "SOL", "USDT", "USDC", "BNB"}


# ══════════════════════════════════════════════════════════════════════════
# CT REGISTRY + ROTATION CURSOR
# ══════════════════════════════════════════════════════════════════════════


def load_ct_handles() -> list[str]:
    """Handles from the curated influential-accounts registry (fail-open [])."""
    try:
        with open(INFLUENTIAL_ACCOUNTS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug("ct_shill:registry_load_failed err=%s", str(exc)[:120])
        return []
    handles: list[str] = []
    seen: set[str] = set()
    raw = data.get("handles") or data.get("accounts") or data.get("overlap_with_ct") or []
    for item in raw:
        h = (
            str(item if isinstance(item, str) else (item or {}).get("handle", ""))
            .strip()
            .lstrip("@")
            .lower()
        )
        if h and h not in seen:
            seen.add(h)
            handles.append(h)
    return handles


def _read_cursor(total: int) -> int:
    try:
        with open(_CURSOR_PATH, encoding="utf-8") as fh:
            idx = int(json.load(fh).get("index", 0))
    except (OSError, ValueError, AttributeError):
        idx = 0
    return idx % total if total else 0


def _write_cursor(idx: int) -> None:
    try:
        with open(_CURSOR_PATH, "w", encoding="utf-8") as fh:
            json.dump({"index": idx, "updated": _now_iso()}, fh)
    except OSError as exc:
        logger.debug("ct_shill:cursor_write_failed err=%s", str(exc)[:120])


def next_account_batch(handles: list[str], count: int) -> list[str]:
    """Next rotation slice of the CT registry (pure, cursor-advancing)."""
    if not handles or count <= 0:
        return []
    start = _read_cursor(len(handles))
    batch = [handles[(start + i) % len(handles)] for i in range(min(count, len(handles)))]
    _write_cursor((start + len(batch)) % len(handles))
    return batch


# ══════════════════════════════════════════════════════════════════════════
# WALL OF SHAME — record / update (idempotent, additive to kol_ingest)
# ══════════════════════════════════════════════════════════════════════════


def _norm_handle(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def record_shill(
    handle: str,
    *,
    handle_id: str = "",
    token: str = "",
    tweet_text: str = "",
    tweet_url: str = "",
    screenshot_url: str = "",
    wallet: str = "",
    coordinated: bool = False,
    platform: str = "x",
) -> bool:
    """Add/update one shill in ``wall_of_shame.json`` (idempotent per handle).

    New entries carry the full evidence bundle (tweet text + screenshot URL +
    shill_count / tokens_shilled / last_shill_ts / platforms). Existing
    entries get shill_count incremented, the token appended, and severity
    escalated when coordinated (X + TG). Returns True when the file changed.
    Never raises.
    """
    h = _norm_handle(handle)
    if not h:
        return False
    try:
        data = json.loads(open(WALL_OF_SHAME_PATH, encoding="utf-8").read())  # noqa: SIM115
    except (OSError, ValueError) as exc:
        logger.debug("ct_shill:shame_load_failed err=%s", str(exc)[:120])
        return False
    wall = data.get("wall_of_shame", []) if isinstance(data, dict) else []
    entry = next(
        (
            e
            for e in wall
            if _norm_handle(e.get("handle")) == h or _norm_handle(e.get("handle_id")) == h
        ),
        None,
    )
    changed = False
    if entry is None:
        entry = {
            "handle": h,
            "handle_id": handle_id or h,
            "wallet": wallet or "",
            "wallets": [wallet] if wallet else [],
            "category": "shill",
            "severity": "high" if coordinated else "medium",
            "risk_level": "critical" if coordinated else "high",
            "group": "",
            "evidence_url": tweet_url,
            "evidence": [_evidence_bundle(tweet_text, tweet_url, screenshot_url, platform)],
            "shill_count": 1,
            "last_shill_ts": _now_iso(),
            "tokens_shilled": [token] if token else [],
            "platforms": [platform] if platform else [],
            "coordinated": coordinated,
            "source": "ct_shill_watch",
        }
        wall.append(entry)
        changed = True
    else:
        entry["shill_count"] = int(entry.get("shill_count") or 0) + 1
        entry["last_shill_ts"] = _now_iso()
        tokens = entry.setdefault("tokens_shilled", [])
        if token and token not in tokens:
            tokens.append(token)
            changed = True
        platforms = entry.setdefault("platforms", [])
        if platform and platform not in platforms:
            platforms.append(platform)
            changed = True
        if coordinated and not entry.get("coordinated"):
            entry["coordinated"] = True
            entry["severity"] = "high"
            entry["risk_level"] = "critical"
            changed = True
        if tweet_url and tweet_url != entry.get("evidence_url"):
            entry["evidence_url"] = tweet_url
            changed = True
        ev = entry.setdefault("evidence", [])
        bundle = _evidence_bundle(tweet_text, tweet_url, screenshot_url, platform)
        if bundle and bundle not in ev:
            ev.append(bundle)
            changed = True
        if wallet and not entry.get("wallet"):
            entry["wallet"] = wallet
            entry.setdefault("wallets", []).append(wallet)
            changed = True
    if changed:
        try:
            out = {"updated": _now_iso(), "count": len(wall), "wall_of_shame": wall}
            with open(WALL_OF_SHAME_PATH, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.warning("ct_shill:shame_write_failed err=%s", str(exc)[:120])
            return False
        logger.info(
            "ct_shill:shame_recorded handle=%s token=%s coordinated=%s", h, token, coordinated
        )
    return changed


def _evidence_bundle(
    tweet_text: str, tweet_url: str, screenshot_url: str, platform: str
) -> dict[str, str]:
    bundle: dict[str, str] = {}
    if tweet_text:
        bundle["tweet_text"] = str(tweet_text)[:600]
    if tweet_url:
        bundle["tweet_url"] = str(tweet_url)[:240]
    if screenshot_url:
        bundle["screenshot_url"] = str(screenshot_url)[:400]
    if platform:
        bundle["platform"] = platform
    return bundle


# ══════════════════════════════════════════════════════════════════════════
# SHILL CONFIRMATION (scanner + TG cross-ref)
# ══════════════════════════════════════════════════════════════════════════


async def tg_cross_ref(handle: str) -> bool:
    """True when the backend shill store already has TELEGRAM evidence for
    this handle → the account shills on BOTH platforms → coordinated."""
    try:
        from rugmunchbot.services import shill_api

        data = await shill_api.get_evidence(account=_norm_handle(handle), limit=50)
        rows = (data or {}).get("evidence") or (data or {}).get("rows") or []
        return any(
            str(r.get("source") or "").lower() == "telegram" for r in rows if isinstance(r, dict)
        )
    except Exception as exc:
        logger.debug("ct_shill:tg_crossref_failed err=%s", str(exc)[:120])
        return False


async def confirm_shill(handle: str, token: str, chain: str) -> tuple[bool, bool]:
    """(confirmed, coordinated) for one (handle, token) promotion.

    Confirmed when the canonical scanner flags the token HIGH/CRITICAL
    (honeypot / rug pattern / LP pull) OR the handle already has TG shill
    evidence (cross-platform repeat offender). Coordinated = TG cross-hit.
    Fail-open (False, False).
    """
    coordinated = await tg_cross_ref(handle)
    if coordinated:
        return True, True
    try:
        from rugmunchbot.channels.kol_discovery import _is_risky_token

        if await _is_risky_token(token, chain):
            return True, False
    except Exception as exc:
        logger.debug("ct_shill:risky_check_failed err=%s", str(exc)[:120])
    return False, False


# ══════════════════════════════════════════════════════════════════════════
# QDRANT social_intel EMBED
# ══════════════════════════════════════════════════════════════════════════


async def embed_shill(record: dict[str, Any]) -> bool:
    """Upsert one shill record into the Qdrant ``social_intel`` collection.

    Direct upsert (same pattern as ``shill_watch._persist_shill_evidence`` —
    the logical-collection resolver can mis-redirect, so we hit the canonical
    endpoint with the physical name). Deterministic point id per handle.
    Fail-open False.
    """
    handle = _norm_handle(record.get("handle"))
    if not handle:
        return False
    try:
        from rugmunchbot.channels.telegram_news._dedup import _embed_candidate

        tokens = ", ".join(str(t) for t in (record.get("tokens_shilled") or [])[:10])
        content = (
            f"Shill: {handle}\nTokens shilled: {tokens}\n"
            f"Shill count: {record.get('shill_count')}\nCoordinated: {record.get('coordinated')}\n"
            f"Evidence: {record.get('evidence_url')}"
        ).strip()
        vector = await _embed_candidate(content)
        if not vector:
            return False
        digest = hashlib.sha256(f"shill:{handle}".encode()).hexdigest()
        point_id = uuid.UUID(int=int(digest, 16) % (2**96), version=4)
        payload = {
            "doc_id": f"shill:{digest[:20]}",
            "kind": "kol_shill",
            "handle": handle,
            "handle_id": record.get("handle_id") or handle,
            "wallet": record.get("wallet") or "",
            "tokens_shilled": (record.get("tokens_shilled") or [])[:20],
            "shill_count": record.get("shill_count"),
            "coordinated": bool(record.get("coordinated")),
            "severity": record.get("severity") or "",
            "evidence_url": record.get("evidence_url") or "",
            "last_shill_ts": record.get("last_shill_ts") or "",
            "captured_at": _now_iso(),
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.put(
                f"{_QDRANT_URL}/collections/{SOCIAL_INTEL_COLLECTION}/points",
                params={"wait": True},
                json={"points": [{"id": str(point_id), "vector": vector, "payload": payload}]},
            )
        ok = resp.status_code in (200, 202)
        if not ok:
            logger.debug("ct_shill:qdrant_status=%d body=%s", resp.status_code, resp.text[:160])
        return ok
    except Exception as exc:
        logger.debug("ct_shill:embed_failed err=%s", str(exc)[:160])
        return False


# ══════════════════════════════════════════════════════════════════════════
# THE 15-MIN TICK — CT timeline ingestion → shill catching
# ══════════════════════════════════════════════════════════════════════════


async def run_ct_shill_watch(ctx: Any = None) -> int:
    """One CT-timeline tick: rotate accounts → pull timelines → catch shills.

    Returns the number of confirmed shills recorded. Budget: at most
    ``CT_SHILL_ACCOUNTS_PER_TICK`` accounts AND never more ApiPass creates
    than ``task_creates_remaining()`` allows (2 per account). Fail-open.
    """
    if not CT_SHILL_ENABLED:
        return 0
    try:
        from rugmunchbot.tools import apipass_x
    except Exception as exc:
        logger.debug("ct_shill:apipass_import_failed err=%s", str(exc)[:120])
        return 0
    if not apipass_x.api_key_configured():
        logger.debug("ct_shill:skipped_no_key")
        return 0

    handles = load_ct_handles()
    if not handles:
        return 0
    # 2 creates per account (handle→id resolve + timeline); clamp to budget.
    budget_accounts = max(0, apipass_x.task_creates_remaining() // 2)
    count = min(CT_SHILL_ACCOUNTS_PER_TICK, budget_accounts)
    batch = next_account_batch(handles, count)
    if not batch:
        return 0

    try:
        from rugmunchbot.channels.kol_discovery import extract_tweet_tokens
        from rugmunchbot.tools.shill_shot import capture_and_ocr
    except Exception as exc:
        logger.debug("ct_shill:helper_import_failed err=%s", str(exc)[:120])
        return 0

    recorded = 0
    shots_left = CT_SHILL_SCREENSHOT_BUDGET
    seen_tweets: set[str] = set()
    for handle in batch:
        if recorded >= CT_SHILL_MAX_ADDS_PER_TICK:
            break
        try:
            tweets = await apipass_x.fetch_user_tweets(handle, limit=CT_SHILL_TWEETS_PER_ACCOUNT)
        except Exception as exc:
            logger.debug("ct_shill:timeline_failed handle=%s err=%s", handle, str(exc)[:120])
            continue
        for tweet in tweets:
            if recorded >= CT_SHILL_MAX_ADDS_PER_TICK:
                break
            text = str(tweet.get("text") or "")
            tweet_key = str(tweet.get("id") or tweet.get("url") or f"{handle}:{text[:80]}")
            if tweet_key in seen_tweets:
                continue  # same tweet surfaced under two batch accounts
            seen_tweets.add(tweet_key)
            tokens = [t for t in extract_tweet_tokens(text) if t not in _BLUECHIP]
            if not tokens:
                continue
            engagement = int(tweet.get("like_count") or 0) + int(tweet.get("retweet_count") or 0)
            if engagement < CT_SHILL_MIN_ENGAGEMENT:
                continue
            token = tokens[0]
            chain = "ticker" if token.startswith("$") else "ethereum"
            confirmed, coordinated = await confirm_shill(handle, token, chain)
            if not confirmed:
                continue
            screenshot_url = ""
            if shots_left > 0 and tweet.get("url"):
                try:
                    shot = await capture_and_ocr(str(tweet["url"]))
                    if shot.get("image_path"):
                        screenshot_url = shot["image_path"]
                        shots_left -= 1
                except Exception as exc:
                    logger.debug("ct_shill:screenshot_failed err=%s", str(exc)[:120])
            saved = record_shill(
                str(tweet.get("handle") or handle),  # tweet author is authoritative
                handle_id=str(tweet.get("author_id") or tweet.get("handle") or handle),
                token=token,
                tweet_text=text,
                tweet_url=str(tweet.get("url") or ""),
                screenshot_url=screenshot_url,
                coordinated=coordinated,
                platform="x",
            )
            if saved:
                recorded += 1
                # Mirror evidence to the backend store + embed into social_intel.
                try:
                    from rugmunchbot.services import shill_api

                    await shill_api.add_evidence(
                        {
                            "token": token,
                            "chain": chain,
                            "account": handle,
                            "x_url": str(tweet.get("url") or ""),
                            "url": str(tweet.get("url") or ""),
                            "image_path": screenshot_url,
                            "text": text[:1200],
                            "source": "x",
                        }
                    )
                except Exception as exc:
                    logger.debug("ct_shill:evidence_mirror_failed err=%s", str(exc)[:120])
                try:
                    shame = _load_shame_entry(handle)
                    if shame:
                        await embed_shill(shame)
                except Exception as exc:
                    logger.debug("ct_shill:embed_step_failed err=%s", str(exc)[:120])
    if recorded:
        logger.info("ct_shill:tick_done batch=%s recorded=%s", ",".join(batch), recorded)
    return recorded


def _load_shame_entry(handle: str) -> dict[str, Any] | None:
    try:
        data = json.loads(open(WALL_OF_SHAME_PATH, encoding="utf-8").read())  # noqa: SIM115
        h = _norm_handle(handle)
        return next(
            (e for e in data.get("wall_of_shame", []) if _norm_handle(e.get("handle")) == h),
            None,
        )
    except (OSError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════════
# DAILY REFRESH — rescore the wall + discover KOLs from shill retweeters
# ══════════════════════════════════════════════════════════════════════════


def score_kol(entry: dict[str, Any]) -> float:
    """Shill-severity score: tokens shilled x coordination x recency.

    Pure. Higher = worse. Used by the daily refresh to keep ``severity``
    honest as evidence accumulates.
    """
    tokens = len(entry.get("tokens_shilled") or [])
    count = int(entry.get("shill_count") or 0)
    coordinated = 2.0 if entry.get("coordinated") else 1.0
    recency = 1.0
    ts = str(entry.get("last_shill_ts") or "")
    if ts:
        try:
            from datetime import datetime

            age_h = (datetime.now(UTC) - datetime.fromisoformat(ts)).total_seconds() / 3600.0
            recency = max(0.25, 1.0 - min(age_h, 720) / 720.0)
        except ValueError:
            recency = 0.5
    return round((tokens * 1.5 + count) * coordinated * recency, 2)


def rescore_wall_of_shame() -> int:
    """Recompute severity for every wall entry from its shill history."""
    try:
        data = json.loads(open(WALL_OF_SHAME_PATH, encoding="utf-8").read())  # noqa: SIM115
    except (OSError, ValueError) as exc:
        logger.debug("ct_shill:rescore_load_failed err=%s", str(exc)[:120])
        return 0
    wall = data.get("wall_of_shame", []) if isinstance(data, dict) else []
    changed = 0
    for entry in wall:
        if entry.get("source") != "ct_shill_watch" and not entry.get("shill_count"):
            continue  # only rescore entries this pipeline owns/annotates
        score = score_kol(entry)
        entry["shill_score"] = score
        if score >= 6:
            sev, risk = "high", "critical"
        elif score >= 2:
            sev, risk = "medium", "high"
        else:
            sev, risk = "low", "watch"
        if (entry.get("severity"), entry.get("risk_level")) != (sev, risk) and entry.get(
            "source"
        ) == "ct_shill_watch":
            entry["severity"], entry["risk_level"] = sev, risk
            changed += 1
    if changed:
        try:
            out = {"updated": _now_iso(), "count": len(wall), "wall_of_shame": wall}
            with open(WALL_OF_SHAME_PATH, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.warning("ct_shill:rescore_write_failed err=%s", str(exc)[:120])
            return 0
    return changed


def _extract_user_nodes(payload: Any) -> list[dict[str, Any]]:
    """User nodes in a retweeters payload (walks the tree, fail-open [])."""
    nodes: list[dict[str, Any]] = []
    seen: set[int] = set()

    def _walk(value: Any, depth: int = 0) -> None:
        if depth > 8 or id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, dict):
            legacy = value.get("legacy") or {}
            if isinstance(legacy, dict) and legacy.get("screen_name"):
                nodes.append(value)
                return
            for child in value.values():
                _walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                _walk(child, depth + 1)

    _walk(payload)
    return nodes[:50]


async def discover_from_retweeters() -> int:
    """Pull retweeters of the newest shill evidence → untracked handles are
    NEW KOL candidates → ``discovered_kols.json`` (one governed ApiPass pull).

    Fail-open 0.
    """
    try:
        from rugmunchbot.channels.kol_discovery import add_discovered_kol, load_tracked_handles
        from rugmunchbot.services import shill_api
        from rugmunchbot.tools import apipass_x
    except Exception as exc:
        logger.debug("ct_shill:retweeter_import_failed err=%s", str(exc)[:120])
        return 0
    if not apipass_x.api_key_configured() or apipass_x.task_creates_remaining() < 1:
        return 0
    data = await shill_api.get_evidence(limit=20)
    rows = (data or {}).get("evidence") or (data or {}).get("rows") or []
    tweet_ids = [
        str(r.get("x_url") or "").rsplit("/status/", 1)[-1].split("?")[0]
        for r in rows
        if isinstance(r, dict) and "/status/" in str(r.get("x_url") or "")
    ][:KOL_DAILY_MAX_RETWEETER_PULLS]
    tracked = load_tracked_handles()
    added = 0
    for tweet_id in tweet_ids:
        if not tweet_id.isdigit():
            continue
        try:
            input_body = apipass_x._build_input("twitter/simple/retweeters", {"tweet_id": tweet_id})
            result = await apipass_x.run_task(
                f"{apipass_x.APIPASS_TWITTER_MODEL}/retweeters", input_body
            )
        except Exception as exc:
            logger.debug("ct_shill:retweeter_pull_failed err=%s", str(exc)[:120])
            continue
        for user in _extract_user_nodes(result):
            handle = _norm_handle(apipass_x._first(user, "screen_name", "username"))
            if not handle or handle in tracked:
                continue
            if add_discovered_kol(
                handle, "retweeter_of_shill", {"tweet_id": tweet_id, "source": "ct_shill_watch"}
            ):
                added += 1
                tracked.add(handle)
    return added


async def run_kol_daily_refresh(ctx: Any = None) -> int:
    """Daily KOL improvement: rescore the wall + retweeter discovery.

    Returns the number of rescored entries (discovery count is logged).
    Fail-open.
    """
    if not KOL_DAILY_REFRESH_ENABLED:
        return 0
    rescored = rescore_wall_of_shame()
    try:
        discovered = await discover_from_retweeters()
    except Exception as exc:
        logger.debug("ct_shill:retweeter_discovery_failed err=%s", str(exc)[:120])
        discovered = 0
    logger.info("ct_shill:daily_refresh rescored=%s discovered=%s", rescored, discovered)
    return rescored


__all__ = [
    "CT_SHILL_ENABLED",
    "CT_SHILL_INTERVAL_MIN",
    "KOL_DAILY_INTERVAL_MIN",
    "SOCIAL_INTEL_COLLECTION",
    "WALL_OF_SHAME_PATH",
    "confirm_shill",
    "discover_from_retweeters",
    "embed_shill",
    "load_ct_handles",
    "next_account_batch",
    "record_shill",
    "rescore_wall_of_shame",
    "run_ct_shill_watch",
    "run_kol_daily_refresh",
    "score_kol",
    "tg_cross_ref",
]
