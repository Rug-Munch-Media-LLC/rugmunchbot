"""KOL discovery from X shill monitoring — find NEW KOLs pushing scams.

Mission (2026-08-31): while the bot watches X for shill activity
(``channels.shill_watch`` + ``tools.apipass_x``), accounts that are NOT in
our tracked KOL universe but are pushing scanner-flagged risky tokens with
real engagement are exactly the new KOLs we want to track. This module:

  1. Pulls candidate tweets via the existing ApiPass client
     (``tools.apipass_x.search_tweets`` — ONE createTask per searched token,
     governed by the ≤ ``RMI_APIPASS_MAX_CALLS_PER_HOUR`` budget governor).
  2. Extracts token identities from each tweet ($CASHTAG / address).
  3. Runs the searched token through the canonical scanner
     (``shill_watch.build_scanner_evidence``) — a token is "risky" when the
     scanner returns HIGH/CRITICAL risk or concrete red-line bullets
     (honeypot, rug pattern, LP pull, deployer history).
  4. Detects UNTRACKED handles (not in wall_of_shame.json, not in
     verified_kol_wallets.json) whose tweet pushes a risky token with
     engagement (likes + retweets) >= ``KOL_DISCOVERY_MIN_ENGAGEMENT``.
  5. Auto-adds them to ``discovered_kols.json`` with status "discovered",
     the token they shilled, and the tweet evidence (id, url, engagement).

Fail-open everywhere: no ApiPass key, rate-limited budget, scanner outage,
or a read-only data dir degrades to zero discoveries — never raises. The
storage path is env-overridable (``RMI_DISCOVERED_KOLS_PATH``) so ops can
point the bot and the backend (``GET /api/v1/kol/discovered``) at the SAME
file for a zero-copy handoff.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.kol_discovery")

KOL_DISCOVERY_ENABLED = os.getenv("RMI_KOL_DISCOVERY_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
#: Engagement floor (likes + retweets) for a shill tweet to count as reach.
KOL_DISCOVERY_MIN_ENGAGEMENT = int(os.getenv("RMI_KOL_DISCOVERY_MIN_ENGAGEMENT", "25"))
#: Max new KOLs auto-added per run (bounded — no list spam).
KOL_DISCOVERY_MAX_ADDS_PER_RUN = int(os.getenv("RMI_KOL_DISCOVERY_MAX_ADDS", "3"))
#: Max X searches per run (each is one ApiPass createTask; the governor in
#: ``apipass_x`` is the hard ceiling — this keeps discovery polite within it).
KOL_DISCOVERY_MAX_SEARCHES = int(os.getenv("RMI_KOL_DISCOVERY_MAX_SEARCHES", "2"))
#: Max tweets inspected per search.
KOL_DISCOVERY_TWEETS_PER_SEARCH = int(os.getenv("RMI_KOL_DISCOVERY_TWEETS_PER_SEARCH", "20"))
#: Storage for discovered KOLs. Defaults to the bot's bundled data dir;
#: override to share one file with the backend's discovered-KOL route.
DISCOVERED_KOLS_PATH = os.getenv(
    "RMI_DISCOVERED_KOLS_PATH",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "data",
        "kol",
        "discovered_kols.json",
    ),
)

#: Token identities in tweet text: $CASHTAG + EVM 0x / Solana base58 address
#: (same shape as the backend's ``x_ingest.extract_token_signals``).
_CASHTAG_RE = re.compile(r"\$([A-Za-z]{2,10})\b")
_TOKEN_ADDRESS_RE = re.compile(r"\b(?:0x[a-fA-F0-9]{6,64}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")
_BLUECHIP = {"BTC", "ETH", "SOL", "USDT", "USDC", "BNB"}


# ══════════════════════════════════════════════════════════════════════════
# TRACKED UNIVERSE — who is already a known KOL (fail-open)
# ══════════════════════════════════════════════════════════════════════════


def _norm_handle(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def load_tracked_handles() -> set[str]:
    """Handles already tracked: wall_of_shame + verified_kol_wallets.

    Fail-open: unreadable files contribute nothing (an empty tracked set
    only widens discovery, never breaks the tick).
    """
    tracked: set[str] = set()
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "kol")
    try:
        with open(os.path.join(data_dir, "wall_of_shame.json"), encoding="utf-8") as fh:
            shame = json.load(fh)
        for entry in shame.get("wall_of_shame") or []:
            for key in ("handle", "handle_id"):
                h = _norm_handle(entry.get(key))
                if h:
                    tracked.add(h)
    except (OSError, ValueError, AttributeError) as exc:
        logger.debug("kol_discovery:shame_load_failed err=%s", str(exc)[:120])
    try:
        with open(os.path.join(data_dir, "verified_kol_wallets.json"), encoding="utf-8") as fh:
            wallets = json.load(fh)
        for entry in wallets.get("wallets") or []:
            h = _norm_handle(entry.get("handle"))
            if h:
                tracked.add(h)
    except (OSError, ValueError, AttributeError) as exc:
        logger.debug("kol_discovery:wallets_load_failed err=%s", str(exc)[:120])
    return tracked


# ══════════════════════════════════════════════════════════════════════════
# PURE DETECTION
# ══════════════════════════════════════════════════════════════════════════


def extract_tweet_tokens(text: str) -> list[str]:
    """Token identities in tweet text: non-bluechip $TICKERs + addresses.

    De-duplicated, order-preserving (cashtags first). Mirrors the backend's
    ``x_ingest.extract_token_signals`` so both rails share one vocabulary.
    """
    text = str(text or "")
    out: list[str] = []
    seen: set[str] = set()
    for sym in _CASHTAG_RE.findall(text):
        up = sym.upper()
        if up in _BLUECHIP or f"${up}" in seen:
            continue
        seen.add(f"${up}")
        out.append(f"${up}")
    for addr in _TOKEN_ADDRESS_RE.findall(text):
        if addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def detect_new_kols(
    tweets: list[dict[str, Any]],
    *,
    tracked_handles: set[str] | None = None,
    risky_tokens: set[str] | None = None,
    min_engagement: int = KOL_DISCOVERY_MIN_ENGAGEMENT,
) -> list[dict[str, Any]]:
    """Untracked handles pushing risky tokens with real engagement.

    Pure + deterministic. ``tweets`` are normalized ApiPass tweet dicts
    (``apipass_x.normalize_tweet`` shape). A tweet qualifies when it names a
    token in ``risky_tokens`` (case-insensitive match on $TICKER or address)
    and ``like_count + retweet_count >= min_engagement``. The author handle
    must be non-empty and NOT in ``tracked_handles``. Returns one discovery
    record per qualifying (handle, token) pair, best engagement first.
    """
    tracked = {t.lower() for t in (tracked_handles or set())}
    risky = {str(t).strip().upper().lstrip("$") for t in (risky_tokens or set()) if str(t).strip()}
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for tweet in tweets or []:
        if not isinstance(tweet, dict):
            continue
        handle = _norm_handle(tweet.get("handle"))
        if not handle or handle in tracked:
            continue
        engagement = int(tweet.get("like_count") or 0) + int(tweet.get("retweet_count") or 0)
        if engagement < max(0, min_engagement):
            continue
        tweet_id = str(tweet.get("tweet_id") or "").strip()
        text = str(tweet.get("text") or "")
        for token in extract_tweet_tokens(text):
            if token.upper().lstrip("$") not in risky:
                continue
            key = (handle, token.upper())
            prev = found.get(key)
            if prev is None or engagement > int(prev.get("engagement") or 0):
                found[key] = {
                    "handle": handle,
                    "token": token.upper(),
                    "engagement": engagement,
                    "tweet_id": tweet_id,
                    "url": str(tweet.get("url") or ""),
                    "text": text[:200],
                    "likes": int(tweet.get("like_count") or 0),
                    "retweets": int(tweet.get("retweet_count") or 0),
                }
    return sorted(found.values(), key=lambda d: -int(d.get("engagement") or 0))


# ══════════════════════════════════════════════════════════════════════════
# PERSISTENCE — discovered_kols.json (dedupe by handle, merge evidence)
# ══════════════════════════════════════════════════════════════════════════


def load_discovered_kols(path: str | None = None) -> list[dict[str, Any]]:
    """Discovered-KOL records (fail-open ``[]``)."""
    p = path or DISCOVERED_KOLS_PATH
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        items = data.get("discovered") if isinstance(data, dict) else data
        return [e for e in (items or []) if isinstance(e, dict)]
    except (OSError, ValueError, TypeError):
        return []


def add_discovered_kol(
    handle: str,
    token: str,
    evidence: dict[str, Any],
    *,
    path: str | None = None,
) -> bool:
    """Add (or merge evidence into) one discovered KOL record.

    Dedupe: one record per handle; repeat discoveries append the token to
    ``tokens_shilled`` and the tweet to ``evidence`` (bounded to 20 entries).
    Returns True when the file was written. Fail-open: a read-only dir logs
    and returns False — discovery never breaks the tick.
    """
    handle = _norm_handle(handle)
    token = str(token or "").strip().upper()
    if not handle or not token:
        return False
    p = path or DISCOVERED_KOLS_PATH
    records = load_discovered_kols(p)
    now = datetime.now(UTC).isoformat()
    record = next((r for r in records if _norm_handle(r.get("handle")) == handle), None)
    if record is None:
        record = {
            "handle": handle,
            "status": "discovered",
            "discovered_at": now,
            "tokens_shilled": [],
            "evidence": [],
        }
        records.append(record)
    tokens = [str(t) for t in (record.get("tokens_shilled") or [])]
    if token and token not in tokens:
        tokens.append(token)
        record["tokens_shilled"] = tokens[:20]
    ev = dict(evidence or {})
    ev_list = [e for e in (record.get("evidence") or []) if isinstance(e, dict)]
    if ev := {k: str(v) for k, v in ev.items() if v}:
        if not any(
            str(e.get("tweet_id") or "") == ev.get("tweet_id", "") and ev.get("tweet_id")
            for e in ev_list
        ):
            ev_list.append(ev)
    record["evidence"] = ev_list[-20:]
    record["last_seen_at"] = now
    try:
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        payload = {
            "updated": now,
            "count": len(records),
            "discovered": records,
        }
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return True
    except OSError as exc:
        logger.debug("kol_discovery:persist_failed err=%s", str(exc)[:120])
        return False


# ══════════════════════════════════════════════════════════════════════════
# RUNNER — budget-bounded X sweep → risky-token scan → auto-add
# ══════════════════════════════════════════════════════════════════════════


def _seed_tokens(limit: int) -> list[str]:
    """Candidate tokens to search X for: recent shill-evidence tokens.

    Reads the bot's local ``shill_evidence`` table (tokens the shill watcher
    already captured) — those are tokens with live shill activity. Fail-open
    ``[]``. Bounded to ``limit`` distinct tokens.
    """
    tokens: list[str] = []
    try:
        from rugmunchbot.db._base import get_db

        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT DISTINCT token FROM shill_evidence WHERE token != '' ORDER BY id DESC LIMIT ?",
                (max(1, int(limit) * 3),),
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            token = str(row[0] or "").strip()
            if token and token not in tokens:
                tokens.append(token)
    except Exception as exc:  # fail-open by contract
        logger.debug("kol_discovery:seed_tokens_failed err=%s", str(exc)[:120])
    return tokens[: max(1, limit)]


async def _is_risky_token(token: str, chain: str) -> bool:
    """True when the canonical scanner flags the token HIGH/CRITICAL risky.

    Reuses ``shill_watch.build_scanner_evidence`` (honeypot / rug pattern /
    LP pull / deployer history). Ticker-only tokens cannot be scanned → not
    risky (no address to resolve). Fail-open False.
    """
    try:
        from rugmunchbot.channels.shill_watch import build_scanner_evidence

        evidence = await build_scanner_evidence(token, chain)
    except Exception as exc:  # scanner outage never blocks discovery
        logger.debug("kol_discovery:scanner_failed token=%s err=%s", token[:24], str(exc)[:120])
        return False
    return str(evidence.get("risk_level") or "").upper() in ("HIGH", "CRITICAL")


async def run_kol_discovery(ctx: Any = None) -> int:
    """One discovery pass: search X for shilled tokens → auto-add new KOLs.

    Budget: at most ``KOL_DISCOVERY_MAX_SEARCHES`` ApiPass createTasks (the
    apipass_x governor is the hard ceiling) and at most
    ``KOL_DISCOVERY_MAX_ADDS_PER_RUN`` auto-adds. Returns the number of new
    KOL records written. Fail-open at every stage.
    """
    if not KOL_DISCOVERY_ENABLED:
        return 0
    try:
        from rugmunchbot.tools import apipass_x
    except Exception as exc:  # fail-open by contract
        logger.debug("kol_discovery:apipass_import_failed err=%s", str(exc)[:120])
        return 0
    if not apipass_x.api_key_configured():
        logger.debug("kol_discovery:skipped_no_key")
        return 0

    tracked = load_tracked_handles()
    seeds = _seed_tokens(KOL_DISCOVERY_MAX_SEARCHES)
    if not seeds:
        logger.debug("kol_discovery:no_seed_tokens")
        return 0

    added = 0
    risky_cache: dict[str, bool] = {}
    for token in seeds:
        if added >= KOL_DISCOVERY_MAX_ADDS_PER_RUN:
            break
        # ONE createTask per searched token (governed inside apipass_x).
        try:
            tweets = await apipass_x.search_tweets(token, limit=KOL_DISCOVERY_TWEETS_PER_SEARCH)
        except Exception as exc:  # per-token fail-open
            logger.debug("kol_discovery:search_failed token=%s err=%s", token[:24], str(exc)[:120])
            continue
        if not tweets:
            continue
        # Risky check: only tokens the scanner flags as risky qualify. Cache
        # per run so repeated mentions cost one scan.
        if token not in risky_cache:
            chain = "ticker" if token.startswith("$") else "ethereum"
            risky_cache[token] = await _is_risky_token(token, chain)
        if not risky_cache[token]:
            continue
        for discovery in detect_new_kols(tweets, tracked_handles=tracked, risky_tokens={token}):
            if added >= KOL_DISCOVERY_MAX_ADDS_PER_RUN:
                break
            saved = add_discovered_kol(
                discovery["handle"],
                discovery["token"],
                {
                    "tweet_id": discovery.get("tweet_id", ""),
                    "url": discovery.get("url", ""),
                    "text": discovery.get("text", ""),
                    "likes": discovery.get("likes", 0),
                    "retweets": discovery.get("retweets", 0),
                    "engagement": discovery.get("engagement", 0),
                    "captured_at": datetime.now(UTC).isoformat(),
                },
            )
            if saved:
                added += 1
                logger.info(
                    "kol_discovery:added handle=%s token=%s engagement=%s",
                    discovery["handle"],
                    discovery["token"],
                    discovery.get("engagement", 0),
                )
    if added:
        logger.info("kol_discovery:run_done added=%s", added)
    return added


__all__ = [
    "DISCOVERED_KOLS_PATH",
    "KOL_DISCOVERY_ENABLED",
    "KOL_DISCOVERY_MAX_ADDS_PER_RUN",
    "KOL_DISCOVERY_MAX_SEARCHES",
    "KOL_DISCOVERY_MIN_ENGAGEMENT",
    "add_discovered_kol",
    "detect_new_kols",
    "extract_tweet_tokens",
    "load_discovered_kols",
    "load_tracked_handles",
    "run_kol_discovery",
]
