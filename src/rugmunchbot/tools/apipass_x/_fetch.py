"""ApiPass X — public fetchers + persistent profile cache.

References the package namespace (``_pkg``) for shared config, cache state,
and the task/normalize helpers so monkeypatching the package propagates.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from typing import Any

import rugmunchbot.tools.apipass_x as _pkg

# ── Persistent screen_name → rich-profile cache (disk-backed) ──────────────
# The value lever on a small budget: pull a profile once, reuse it. Cache is a
# per-handle JSON file under ``APIPASS_CACHE_DIR``; TTL is ``APIPASS_PROFILE_CACHE_TTL``
# (active) / ``APIPASS_SUSPEND_CACHE_TTL`` (unavailable). Fail-open: a cache
# read/write error never blocks a pull or a miss.


def _cache_path(handle: str) -> str:
    return os.path.join(
        _pkg.APIPASS_CACHE_DIR, f"{hashlib.sha256(handle.lower().encode()).hexdigest()[:24]}.json"
    )


def _cache_probe() -> bool:
    """Once-per-process check that the cache dir is writable (fail-open)."""
    if _pkg._CACHE_PROBED:
        return _pkg._CACHE_OK
    _pkg._CACHE_PROBED = True
    try:
        os.makedirs(_pkg.APIPASS_CACHE_DIR, exist_ok=True)
        _pkg._CACHE_OK = os.path.isdir(_pkg.APIPASS_CACHE_DIR) and os.access(
            _pkg.APIPASS_CACHE_DIR, os.W_OK
        )
    except OSError:
        _pkg._CACHE_OK = False
    return _pkg._CACHE_OK


def _cache_read(handle: str) -> dict[str, Any] | None:
    """Read a cached profile for *handle*; returns the stored dict or None.

    Expired entries (past their TTL) are treated as a miss and deleted. Returns
    a deep-copied dict so callers never mutate the cache in place.
    """
    if not _cache_probe():
        return None
    path = _cache_path(handle)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        ttl = float(data.get("ttl") or 0.0)
        fetched = float(data.get("fetched_at_ms") or 0.0)
        if ttl <= 0 or (time.time() * 1000.0 - fetched) > ttl * 1000.0:
            with contextlib.suppress(OSError):
                os.remove(path)
            return None
        return dict(data.get("profile") or {})
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _cache_write(handle: str, profile: dict[str, Any], ttl: float) -> bool:
    """Persist *profile* for *handle* with *ttl* seconds. Returns success."""
    if not _cache_probe():
        return False
    try:
        payload = {
            "handle": handle.lower().lstrip("@"),
            "fetched_at_ms": time.time() * 1000.0,
            "ttl": float(ttl),
            "profile": profile,
        }
        with open(_cache_path(handle), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        return True
    except OSError:
        return False


def _cache_clear(handle: str | None = None) -> int:
    """Evict cache entries (one handle or the whole cache). Returns count."""
    removed = 0
    if not _cache_probe():
        return 0
    try:
        if handle:
            p = _cache_path(handle)
            if os.path.isfile(p):
                os.remove(p)
                removed = 1
        else:
            for name in os.listdir(_pkg.APIPASS_CACHE_DIR):
                if name.endswith(".json"):
                    try:
                        os.remove(os.path.join(_pkg.APIPASS_CACHE_DIR, name))
                        removed += 1
                    except OSError:
                        pass
    except OSError:
        pass
    return removed


# ── Rich X-profile normalization (the richest single-pull contract) ────────
# ``twitter/graphql/user-by-screen-name`` returns the full user node. For an
# ACTIVE user the node carries ``core`` (name/screen_name/created_at),
# ``avatar`` (image), ``location``, ``verification``, ``is_blue_verified``,
# ``verification_info``, ``rest_id`` and ``legacy`` (description + all the
# counts). For an UNAVAILABLE account the node is ``UserUnavailable`` carrying
# verbatim ``reason``/``message`` (e.g. "User is suspended"). We normalize BOTH
# and never fabricate a field that the provider didn't send.


def _find_user_node(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Locate the user node in a graphql user-by-screen-name resultJson.

    Walks the known container shapes (``data.user.result`` and the generic
    containers the existing ``_resolve_user_id`` recognizes) and returns the
    dict that holds ``legacy``/``core``/``rest_id`` — or the ``UserUnavailable``
    node. Fail-open: returns None when no user node is found.
    """
    if not isinstance(payload, dict):
        return None

    def _candidate(v: Any, depth: int = 0) -> dict[str, Any] | None:
        if depth > 6 or not isinstance(v, dict):
            return None
        if any(k in v for k in ("legacy", "core", "rest_id", "id_str")):
            return v
        if v.get("__typename") == "UserUnavailable" or "reason" in v or "message" in v:
            return v
        for key in ("result", "user", "data", "variables"):
            inner = v.get(key)
            if isinstance(inner, dict):
                found = _candidate(inner, depth + 1)
                if found:
                    return found
        for val in v.values():
            if isinstance(val, dict):
                found = _candidate(val, depth + 1)
                if found:
                    return found
        return None

    return _candidate(payload)


def normalize_profile(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a ``user-by-screen-name`` resultJson into the rich X profile.

    Returns a dict with ``available: bool``. Active profiles carry handle,
    user_id, name, join date, avatar/banner, bio, location, verification and a
    full ``metrics`` object. Unavailable accounts carry the verbatim
    ``reason``/``message``/``__typename`` and AVAILABLE=False (the caller caches
    this for ``APIPASS_SUSPEND_CACHE_TTL`` so we never re-spend credits asking
    about a banned account). Returns None when the payload has no user node.
    """
    node = _find_user_node(payload)
    if not isinstance(node, dict):
        return None
    # Unavailable / suspended / deleted.
    if node.get("__typename") == "UserUnavailable" or "reason" in node or "message" in node:
        return {
            "available": False,
            "reason": str(node.get("reason") or node.get("__typename") or ""),
            "message": str(node.get("message") or ""),
            "__typename": str(node.get("__typename") or ""),
        }
    core = _pkg._as_dict(node.get("core"))
    avatar = _pkg._as_dict(node.get("avatar"))
    loc = _pkg._as_dict(node.get("location"))
    ver = _pkg._as_dict(node.get("verification"))
    leg = _pkg._as_dict(node.get("legacy"))
    handle = str(core.get("screen_name") or "").strip().lstrip("@")
    if not handle:
        handle = str(node.get("screen_name") or node.get("username") or "").strip().lstrip("@")
    rest_id = _pkg._decode_user_id(node.get("rest_id") or node.get("id") or "")
    vinfo = _pkg._as_dict(node.get("verification_info"))
    vreason = _pkg._as_dict(vinfo.get("reason"))
    vdesc = _pkg._as_dict(vreason.get("description"))
    pinned = [str(x) for x in (leg.get("pinned_tweet_ids_str") or []) if x]
    return {
        "available": True,
        "handle": handle,
        "user_id": rest_id,
        "name": str(core.get("name") or handle),
        "screen_name": str(core.get("screen_name") or handle),
        "created_at": str(core.get("created_at") or ""),
        "avatar_url": str(avatar.get("image_url") or ""),
        "banner_url": str(leg.get("profile_banner_url") or ""),
        "bio": str(leg.get("description") or ""),
        "location": str(loc.get("location") or ""),
        "website": str(leg.get("url") or ""),
        "verified": bool(ver.get("verified") or False),
        "is_blue_verified": bool(node.get("is_blue_verified") or False),
        "verification_reason": str(vdesc.get("text") or ""),
        "metrics": {
            "followers": _pkg._as_int(leg.get("followers_count")),
            "following": _pkg._as_int(leg.get("friends_count")),
            "statuses": _pkg._as_int(leg.get("statuses_count")),
            "likes": _pkg._as_int(leg.get("favourites_count")),
            "listed": _pkg._as_int(leg.get("listed_count")),
            "media": _pkg._as_int(leg.get("media_count")),
            "fast_followers": _pkg._as_int(leg.get("fast_followers_count")),
            "normal_followers": _pkg._as_int(leg.get("normal_followers_count")),
        },
        "pinned_tweet_ids": pinned,
        "pinned_url": _pkg._tweet_url(handle, pinned[0]) if pinned else "",
        "url": f"https://x.com/{handle}",
        "source": "apipass_x",
    }


async def fetch_x_profile(handle: str, *, force: bool = False) -> dict[str, Any] | None:
    """RICH pull — one ApiPass createTask returns the full X profile + metrics.

    This is the "pull from X as much as we can each pull" contract. It is served
    from the persistent cache first (a handle is pulled once and reused), so
    repeat calls cost ZERO credits. ``force`` bypasses the cache. Fail-open:
    no key / rate limited / API error / malformed payload → None.
    """
    import asyncio

    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return None
    if not _pkg._resolve_api_key():
        _pkg.logger.debug("apipass_x:profile_skipped_no_key")
        return None
    if not force:
        cached = await asyncio.to_thread(_cache_read, handle)
        if cached is not None:
            _pkg.logger.debug("apipass_x:profile_cache_hit handle=%s", handle)
            return dict(cached)
    try:
        result = await _pkg.run_task(
            _pkg._MODEL_USER_BY_SCREEN_NAME, {"variables": {"screen_name": handle}}
        )
    except Exception as exc:  # best-effort fetch, fail-open
        _pkg.logger.debug("apipass_x:profile_fetch_failed handle=%s err=%s", handle, str(exc)[:120])
        return None
    profile = normalize_profile(result)
    if profile is None:
        _pkg.logger.warning("apipass_x:profile_no_node handle=%s", handle)
        return None
    ttl = (
        _pkg.APIPASS_SUSPEND_CACHE_TTL
        if not profile.get("available")
        else _pkg.APIPASS_PROFILE_CACHE_TTL
    )
    await asyncio.to_thread(_cache_write, handle, profile, ttl)
    return profile


async def _resolve_user_id(handle: str) -> str | None:
    """Resolve an X screen name → numeric user id, reusing the cached profile.

    Callers pass handles, but ``twitter/simple/user-tweets`` requires a numeric
    ``user_id``. This now goes through :func:`fetch_x_profile` (the RICH single
    pull) so the profile is pulled once, cached, and the ``rest_id`` is read
    from cache on every subsequent call — the old code made a *separate*
    createTask per resolution, wasting half the budget. Fail-open: any error →
    None (caller returns ``[]``).
    """
    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return None
    try:
        profile = await fetch_x_profile(handle)
    except Exception as exc:  # resolution is best-effort
        _pkg.logger.debug(
            "apipass_x:resolve_user_id_failed handle=%s err=%s", handle, str(exc)[:120]
        )
        return None
    if profile and profile.get("available") and profile.get("user_id"):
        return str(profile["user_id"])
    return None


async def fetch_tweet_detail(tweet_id: str) -> dict[str, Any] | None:
    """One real tweet by id (the ApiPass 'tweet-detail' endpoint)."""
    tweet_id = str(tweet_id or "").strip()
    if not tweet_id:
        return None
    input_body = _pkg._build_input(
        "twitter/simple/tweet-detail",
        {"tweet_id": tweet_id},
    )
    result = await _pkg.run_task(f"{_pkg.APIPASS_TWITTER_MODEL}/tweet-detail", input_body)
    tweets = _pkg._extract_tweets(result)
    return tweets[0] if tweets else None


async def fetch_user_tweets(handle: str, limit: int = 10) -> list[dict[str, Any]]:
    """Recent real tweets from one X account ('user-tweets' endpoint).

    Returns normalized tweet dicts (fail-open ``[]``). The caller owns
    semantic dedup / rerank via :func:`semantic_dedup` / :func:`rerank_tweets`.

    2026-08-29: ``twitter/simple/user-tweets`` takes a numeric ``user_id``, not
    a handle. We only have a handle, so we first resolve handle → user_id via
    the graphql ``user-by-screen-name`` endpoint, then fetch the timeline.
    Fail-open at every hop (no id → ``[]``).
    """
    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return []
    user_id = await _pkg._resolve_user_id(handle)
    if not user_id:
        return []
    input_body = _pkg._build_input(
        "twitter/simple/user-tweets",
        {"user_id": user_id, "count": max(1, min(int(limit), 50))},
    )
    result = await _pkg.run_task(f"{_pkg.APIPASS_TWITTER_MODEL}/user-tweets", input_body)
    return _pkg._extract_tweets(result)[: max(1, min(int(limit), 50))]


async def search_tweets(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """X search results for *query* ('search' endpoint), newest-ish first.

    Fail-open ``[]``. Engaged tweets (likes + retweets) are kept; zero-
    engagement dust is dropped so the channel never posts dead shills.
    """
    query = str(query or "").strip()
    if not query:
        return []
    input_body = _pkg._build_input(
        "twitter/simple/search",
        {"q": query, "count": max(1, min(int(limit) * 2, 50))},
    )
    result = await _pkg.run_task(f"{_pkg.APIPASS_TWITTER_MODEL}/search", input_body)
    tweets = _pkg._extract_tweets(result)
    tweets.sort(
        key=lambda t: int(t.get("like_count") or 0) * 2 + int(t.get("retweet_count") or 0),
        reverse=True,
    )
    return tweets[: max(1, min(int(limit), 50))]
