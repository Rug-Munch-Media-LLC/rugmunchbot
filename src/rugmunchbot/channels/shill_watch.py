"""Coordinated shill-campaign detection — same token pushed by many accounts.

Channel-quality overhaul (2026-08-29), deliverable 4: a shill campaign is a
token (address or ``$TICKER``) that many distinct accounts / KOL call
channels push inside a short window. Legit news coverage is typically a
couple of outlets; a coordinated campaign is N distinct pushers in W hours
— that is the signal, and it belongs on the ALERTS channel
(@rmialerts, high-signal only — alongside exploit + scam/rug detections).

2026-08-30 (social ingest airtight): shills delete tweets, so a text-only
rail loses the evidence. When a campaign is detected we now:
  * capture a cheap screenshot of the X post (``tools.shill_shot`` — one
    DeepInfra VL-4B vision call per shill, bounded),
  * save the screenshot reference + extracted OCR text to the DB and to the
    RAG ``threat_intel_v2`` Qdrant collection (with the source link),
  * rank the campaign by rerank importance (same qwen3-reranker lane as the
    news feed) and route: ``>= main gate`` → @cryptorugmuncher (+ @rmialerts),
    ``>= news floor`` → @rmialerts, below → hold. A coordinated shill is
    danger intel, so it never lands on the news channel.

Pipeline:
  1. ``run_shill_watch`` sweeps the focused KOL/alpha channel list
     (``SHILL_WATCH_CHANNELS`` — bounded, ~11 channels, NOT the full
     200-channel news sweep) for the last ``SHILL_SWEEP_HOURS``.
  2. ``detect_shill_campaigns`` (pure) groups articles by token identity,
     counts DISTINCT pushing channels per token inside the window, and
     flags tokens with >= ``SHILL_MIN_ACCOUNTS`` distinct pushers and
     >= ``SHILL_MIN_MENTIONS`` total mentions.
  3. ``run_shill_watch`` scores each campaign via the reranker
     (``channels.importance.score_importance``), captures X screenshots,
     runs address-token campaigns through the canonical scanner
     (``build_scanner_evidence`` — honeypot/deployer/bundled-supply/LP
     evidence for the "why this is likely a scam" note), persists evidence,
     and posts the routed alert (alerts / alerts+main, held below the floor).

Fail-open everywhere: a dead sweep client, empty sweep, missing X URL,
unreachable vision lane, scanner outage, Redis outage, or DB write failure
never crashes the tick — silence is a valid outcome.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from rugmunchbot.channels.scam_watch._classify import extract_token_addresses
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.shill_watch")

SHILL_WATCH_ENABLED = os.getenv("RMI_SHILL_WATCH_ENABLED", "true").lower() in ("1", "true", "yes")
SHILL_INTERVAL_MIN = int(os.getenv("RMI_SHILL_WATCH_INTERVAL_MIN", "60"))
#: Bounded sweep window (hours) — shill campaigns are fast; no need to look
#: back a week. Must stay <= the news sweep window for consistent freshness.
SHILL_SWEEP_HOURS = int(os.getenv("RMI_SHILL_WATCH_SWEEP_HOURS", "6"))
#: Messages read per focused channel per sweep (bounded).
SHILL_PER_CHANNEL_LIMIT = int(os.getenv("RMI_SHILL_WATCH_PER_CHANNEL_LIMIT", "30"))
#: Distinct pushing accounts that make a token a campaign.
SHILL_MIN_ACCOUNTS = int(os.getenv("RMI_SHILL_MIN_ACCOUNTS", "3"))
#: Minimum total mentions (articles) across those accounts.
SHILL_MIN_MENTIONS = int(os.getenv("RMI_SHILL_MIN_MENTIONS", "3"))
#: Max alerts posted per run (bounded, no spam).
SHILL_MAX_ALERTS = int(os.getenv("RMI_SHILL_MAX_ALERTS", "3"))
#: Detection window for grouping (hours).
SHILL_WINDOW_HOURS = int(os.getenv("RMI_SHILL_WINDOW_HOURS", "24"))

#: 2026-08-30 screenshot capture knobs. A shill deletes its tweet, so the
#: Wall of Shame attaches evidence. The capture is one DeepInfra VL-4B vision
#: call per shill mention and is strictly bounded so a busy campaign can
#: never burn the whole budget. ``RMI_SHILL_SCREENSHOT_ENABLED`` gates it.
SHILL_SCREENSHOT_ENABLED = os.getenv("RMI_SHILL_SCREENSHOT_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
#: Directory for PNG evidence (defaults to a writable generic temp dir —
#: never an internal fleet path, per the methodology-leak policy).
SHILL_SCREENSHOT_DIR = os.getenv(
    "RMI_SHILL_SCREENSHOT_DIR",
    os.path.join(tempfile.gettempdir(), "rmi", "shill_shots"),
)
#: Max screenshots captured per run (across all campaigns).
SHILL_SCREENSHOT_BUDGET = int(os.getenv("RMI_SHILL_SCREENSHOT_BUDGET", "6"))
#: Max screenshots per campaign (one vision call per shill, capped).
SHILL_SCREENSHOT_PER_CAMPAIGN = int(os.getenv("RMI_SHILL_SCREENSHOT_PER_CAMPAIGN", "3"))
#: Max total OCR text chars persisted per screenshot (bounded payload).
_SHILL_OCR_MAX_CHARS = 1200
#: Canonical Qdrant URL + collection for shill/screenshot evidence. The
#: logical-collection resolver (``_vendor.rag_pkg._qdrant``) can redirect a
#: request for ``threat_intel_v2`` to ``rag_documents`` when the existence
#: probe is cached/missed, so we upsert DIRECTLY to the canonical endpoint
#: (```RMI_QDRANT_URL``, default ``http://127.0.0.1:6334`` — the fleet's
#: canonical Qdrant) with the physical collection name.
_QDRANT_URL = os.getenv("RMI_QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
_SHILL_EVIDENCE_COLLECTION = os.getenv("RMI_SHILL_EVIDENCE_COLLECTION", "threat_intel_v2")

#: Focused KOL/alpha/call-analysis channels the shill watcher sweeps.
#: Comma-separated env override (``RMI_SHILL_WATCH_CHANNELS``). Deliberately
#: small — this is the coordinated-pusher signal set, not the news feed.
_DEFAULT_SHILL_CHANNELS: tuple[str, ...] = (
    "CallAnalyser",  # KOL call analysis
    "walloftrophies",  # KOL Trending / CallAnalyser
    "thepumpalert",  # scam call-out channel
    "kamocalls",  # Kamo | Daily trades
    "CryptoCapoTG",  # CryptoCapo — calls + analysis
    "shahlito",  # Shah's Journal — research/calls
    "skeletonlounge",  # Skeleton Lounge Portal — calls
    "rickburpbotnews",  # Rick's Lab — research/alpha
    "dimethyeth",  # dime — research
    "OnChainWizard",  # OnChain Wizard — alpha
    "cryptowhaletrades",  # Crypto Whale Trading — whale/calls
)
SHILL_WATCH_CHANNELS: tuple[str, ...] = tuple(
    c.strip().lstrip("@")
    for c in os.getenv("RMI_SHILL_WATCH_CHANNELS", ",".join(_DEFAULT_SHILL_CHANNELS)).split(",")
    if c.strip()
)


@dataclass(slots=True)
class ShillCampaign:
    """One detected coordinated shill campaign."""

    token: str  # address or $TICKER
    chain: str
    accounts: list[str]  # distinct pushing channels, most-mentions first
    mentions: list[dict[str, str]] = field(default_factory=list)  # {channel, title, url}
    window_start: str = ""
    window_end: str = ""
    score: float = 0.0
    importance: float = 0.0  # rerank importance (2026-08-30 routing)
    screenshots: list[dict[str, str]] = field(default_factory=list)  # {url, image_path, text}
    scanner_evidence: dict[str, Any] = field(
        default_factory=dict
    )  # {scanned, risk_level, safety_score, bullets, note}

    @property
    def key(self) -> str:
        """Stable dedupe identity: token + chain (per-day in the runner)."""
        return f"{self.chain}:{self.token}"


def _posted_at(article: Any) -> datetime:
    """Article timestamp (UTC); falls back to now for undated rows."""
    ts = getattr(article, "posted_at", None)
    if ts is None and isinstance(article, dict):
        ts = article.get("posted_at")
    if ts is None:
        return datetime.now(UTC)
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _article_fields(article: Any) -> tuple[str, str, str, list[str]]:
    """(title, channel, url, entities) for a NewsArticle or dict (fail-open)."""
    if isinstance(article, dict):
        title = str(article.get("title") or article.get("text") or "")[:140]
        channel = str(article.get("source_channel") or article.get("channel") or "unknown")
        url = str(article.get("source_url") or article.get("url") or "")[:240]
        entities = [str(e) for e in (article.get("entities") or [])]
        return title, channel, url, entities
    title = str(getattr(article, "title", "") or "")[:140]
    channel = str(getattr(article, "source_channel", "") or "unknown")
    url = str(getattr(article, "source_url", "") or "")[:240]
    entities = [str(e) for e in (getattr(article, "entities", None) or [])]
    return title, channel, url, entities


def _token_ids(article: Any) -> set[tuple[str, str]]:
    """Token identities for one article: (chain, address) + ("ticker", $SYM).

    Reuses the canonical entity extraction (``scam_watch._classify``
    ``extract_token_addresses``) so shill detection sees exactly the tokens
    the scanner feeds do — one vocabulary, no drift.
    """
    ids: set[tuple[str, str]] = set()
    if isinstance(article, dict):
        # Dicts (tests / synthetic rows) carry entities directly.
        for e in article.get("entities") or []:
            e = str(e)
            if e.startswith("$") and len(e) >= 3:
                ids.add(("ticker", e.upper()))
            elif len(e) >= 32:
                ids.add(("address", e))
        return ids
    try:
        addresses, tickers = extract_token_addresses(article)
    except Exception:  # fail-open — unknown extraction never breaks detection
        return ids
    for addr in addresses:
        ids.add(("address", str(addr.get("address") or "")))
        ids.add(("chain", str(addr.get("chain") or "ethereum")))
    for t in tickers:
        if t:
            ids.add(("ticker", "$" + t.upper()))
    return ids


def detect_shill_campaigns(
    articles: list[Any],
    *,
    window_hours: int = SHILL_WINDOW_HOURS,
    min_accounts: int = SHILL_MIN_ACCOUNTS,
    min_mentions: int = SHILL_MIN_MENTIONS,
) -> list[ShillCampaign]:
    """Flag tokens pushed by >= *min_accounts* distinct channels in a window.

    Pure + deterministic for a fixed input: groups by token identity, counts
    distinct pushing channels and total mentions inside ``window_hours``,
    and returns campaigns sorted by score (accounts*10 + mentions) desc.

    The 1-channel guard: a token mentioned only by one channel (however
    loudly) is coverage/call, NOT a coordinated campaign — the distinct-
    account count is the whole point.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=max(1, window_hours))

    buckets: dict[str, dict[str, Any]] = {}

    def _bucket(token: str, chain: str) -> dict[str, Any]:
        key = f"{chain}:{token}"
        if key not in buckets:
            buckets[key] = {
                "token": token,
                "chain": chain,
                "accounts": {},
                "mentions": [],
                "earliest": now,
            }
        return buckets[key]

    for article in articles or []:
        posted = _posted_at(article)
        if posted < cutoff:
            continue
        title, channel, url, _entities = _article_fields(article)
        token_ids = _token_ids(article)
        for kind, value in token_ids:
            if kind not in ("address", "ticker"):
                continue
            if not value or len(str(value)) < 3:
                continue
            if kind == "ticker" and str(value).upper().lstrip("$") in {
                "BTC",
                "ETH",
                "SOL",
                "USDT",
                "USDC",
                "BNB",
            }:
                # Blue-chip tickers are mentioned by everyone constantly —
                # "coordinated shill" for BTC is noise by definition.
                continue
            b = _bucket(str(value), "ethereum" if kind == "address" else "ticker")
            b["accounts"].setdefault(channel, 0)
            b["accounts"][channel] += 1
            b["earliest"] = min(b["earliest"], posted)
            b["mentions"].append(
                {
                    "channel": channel,
                    "title": title,
                    "url": url,
                }
            )

    campaigns: list[ShillCampaign] = []
    for b in buckets.values():
        accounts = sorted(b["accounts"].items(), key=lambda kv: -kv[1])
        if len(accounts) < min_accounts:
            continue
        mentions = b["mentions"]
        if len(mentions) < min_mentions:
            continue
        campaigns.append(
            ShillCampaign(
                token=b["token"],
                chain=b["chain"],
                accounts=[a for a, _ in accounts],
                mentions=mentions,
                window_start=b["earliest"].isoformat(),
                window_end=now.isoformat(),
                score=len(accounts) * 10.0 + len(mentions),
            )
        )
    return sorted(campaigns, key=lambda c: c.score, reverse=True)


# ── X status URL detection (screenshot evidence target) ────────────────────
# A shill tweet is captured from its public ``x.com/<handle>/status/<id>``
# (or ``twitter.com`` / ``/i/status/``) page. Reuses the same status-id
# recognition the read-tweet tool uses so the two rails share one vocabulary.
_X_STATUS_RE = re.compile(
    r"https?://(?:x\.com|twitter\.com)/(?:[^/]+|i)/status/(\d+)", re.IGNORECASE
)
_X_STATUS_CANON = "https://x.com/i/status/{id}"


def _x_status_url(url: str) -> str:
    """Canonical X status URL for the first status id in ``url`` (or "")."""
    m = _X_STATUS_RE.search(str(url or ""))
    return _X_STATUS_CANON.format(id=m.group(1)) if m else ""


def _esc(value: object) -> str:
    """HTML-escape for Telegram parse_mode=html."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    """Persist one screenshot's reference + OCR text to DB + Qdrant.

    ``shot`` should carry ``url``/``image_path``/``text`` (the capture output).
    Returns True when at least the DB row landed. Never raises.
    """
    token = str(campaign.token or "")
    chain = str(campaign.chain or "")
    account = str(shot.get("account") or "")
    x_url = str(shot.get("url") or "")
    image_path = str(shot.get("image_path") or "")
    ocr = str(shot.get("text") or "")[:_SHILL_OCR_MAX_CHARS]

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

    # Qdrant (scam-intel lane) — source link always rides the metadata so the
    # evidence is traceable back to the original shill. We upsert DIRECTLY to
    # the canonical Qdrant endpoint (``RMI_QDRANT_URL``, default :6334) into
    # ``threat_intel_v2`` (2560-dim Cosine) rather than going through the
    # engine's logical-collection resolver, which can redirect to
    # ``rag_documents`` on a cached probe miss.
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
                    "source": account or "telegram_shill",
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
        if not x_url:
            continue
        out_path = os.path.join(
            SHILL_SCREENSHOT_DIR,
            f"{campaign.key.replace(':', '_')[:48]}_{captured}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.png",
        )
        account = str(mention.get("channel") or "")
        shot = await capture_and_ocr(x_url, out_path=out_path)
        if shot.get("text"):
            shot["account"] = account
            campaign.screenshots.append(
                {k: shot.get(k, "") for k in ("url", "image_path", "text", "account")}
            )
            mention["image_path"] = shot.get("image_path", "")
            mention["ocr_text"] = str(shot.get("text", ""))[:_SHILL_OCR_MAX_CHARS]
            await _persist_shill_evidence(campaign, shot)
            captured += 1
    return captured


async def _score_shill_campaign(campaign: ShillCampaign) -> float:
    """Rerank-importance for one campaign (0.0 on any failure, fail-open).

    Builds a ``NewsArticle`` from the campaign's mention titles/bodies and
    scores it with the same qwen3-reranker lane the news feed uses
    (``channels.importance``). High importance (≥ main gate) signals a
    severe, actively-promoted rug setup → the alert escalates to
    @cryptorugmuncher; below the news floor the campaign is held (never
    spammed). Any reranker outage keeps ``0.0`` so the runner's threshold
    hold applies rather than guessing.
    """
    try:
        from rugmunchbot._vendor.news_ingest.models import NewsArticle
        from rugmunchbot.channels.importance import score_importance

        titles = [str(m.get("title") or "") for m in campaign.mentions]
        body = " ".join(t for t in titles if t)
        article = NewsArticle(
            title=f"Coordinated shill campaign {campaign.token}",
            body=(body or campaign.token)[:1500],
            source_channel=campaign.accounts[0] if campaign.accounts else "shill_watch",
            source_url=_x_status_url(str(campaign.mentions[0].get("url") or ""))
            or "https://rugmunch.io",
            category="scam",
        )
        scored = await score_importance([article])
        if scored:
            campaign.importance = round(scored[0].importance, 4)
            return campaign.importance
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_watch:score_failed err=%s", str(exc)[:120])
    campaign.importance = 0.0
    return 0.0


# ── Shill→scanner evidence (2026-09-01) ────────────────────────────────────
# A detected campaign that names a REAL token address is run through the
# canonical scanner (``scan_token_v2`` → legacy ``scan_token`` fallback) so
# the alert can carry a concrete "why this is likely a scam" note — honeypot,
# deployer history, bundled supply, LP risk — instead of a static boilerplate
# sentence. Fail-open: a scanner outage returns ``{}`` and the alert posts on
# the text-only evidence it already has. Only address tokens resolve; a
# ``$TICKER``-only shill has nothing to scan.
SHILL_SCANNER_EVIDENCE_ENABLED = os.getenv("RMI_SHILL_SCANNER_EVIDENCE", "true").lower() in (
    "1",
    "true",
    "yes",
)


def _looks_like_address(token: str) -> bool:
    """True when ``token`` is a chain address (0x hex / Solana base58 mint).

    Ticker-only shills (``$PEPE``) cannot be scanned — there is no address
    to resolve. Solana mints are base58 (~32-44 chars); EVM are 0x + 40 hex.
    """
    t = str(token or "").strip()
    if not t:
        return False
    if t.startswith("0x"):
        body = t[2:]
        return len(t) >= 34 and all(c in "0123456789abcdefABCDEF" for c in body)
    return 32 <= len(t) <= 44


def _pct(value: object) -> float | None:
    """Coerce a 0..100 percentage to float; None when absent/not a number."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if 0 <= f <= 100 else None


def _evidence_bullets(data: dict[str, Any]) -> list[str]:
    """Build a short, defensible "why this is likely a scam" bullet list.

    Reads BOTH the v2 payload (``risk_level``/``flags``/``evidence``) and the
    legacy scan dict (``honeypot``/``buy_tax``/``bundle_detected``/``lp_locked``
    ...). Only renders a bullet when the scanner returned an actual value that
    crosses a clear red line — never a fabricated or guessed number. Returns
    ``[]`` when nothing defensible is present.
    """
    bullets: list[str] = []

    # ── v2 top-level verdict ──
    risk_level = str(data.get("risk_level") or "").upper()
    safety = data.get("safety_score")
    if risk_level in ("CRITICAL", "HIGH"):
        if isinstance(safety, (int, float)):
            bullets.append(f"Scanner verdict: {risk_level} risk (safety {float(safety):.0f}/100)")
        else:
            bullets.append(f"Scanner verdict: {risk_level} risk")
    flags = data.get("flags") or data.get("risk_flags") or []
    if isinstance(flags, list) and flags:
        bullets.append(f"Scanner flags: {', '.join(str(f)[:60] for f in flags[:4])}")

    evidence = data.get("evidence")
    if isinstance(evidence, dict):
        # Honeypot / security checks.
        sec = evidence.get("security")
        if isinstance(sec, dict):
            try:
                for check in sec.get("checks", {}).values():
                    if isinstance(check, dict) and str(check.get("status", "")).lower() in (
                        "honeypot",
                        "buy_tax",
                        "sell_tax",
                        "can_take_back_ownership",
                        "owner_change_balance",
                    ):
                        bullets.append(
                            f"On-chain check {check.get('status', '?')}: {check.get('details', 'flagged')[:80]}"
                        )
            except Exception:  # defensive — no bullet on a malformed check
                logger.debug("shill_watch:security_checks_parse_failed", exc_info=True)
            rug = sec.get("rug_patterns")
            if isinstance(rug, dict):
                matches = rug.get("match_count") or len(rug.get("patterns") or [])
                if matches:
                    bullets.append(f"Matched {matches} known rug-pull signature(s)")
        # Liquidity / LP risk.
        liq = evidence.get("liquidity")
        if isinstance(liq, dict):
            locked = _pct(liq.get("locked_pct"))
            if locked is not None and locked < 20:
                bullets.append(f"LP only {locked:.0f}% locked — early-exit/removal risk")
        # Holder concentration (bundled supply signal).
        holders = evidence.get("holders")
        if isinstance(holders, dict):
            top10 = _pct(holders.get("top_10_pct"))
            if top10 is not None and top10 >= 60:
                bullets.append(f"Top 10 holders control {top10:.0f}% of supply")
        # Fresh / mixer-funded supply.
        funding = evidence.get("funding_analysis")
        if isinstance(funding, dict):
            fresh = _pct(funding.get("fresh_wallet_pct"))
            if fresh is not None and fresh >= 40:
                bullets.append(f"{fresh:.0f}% of supply held by fresh wallets")
            mixer = _pct(funding.get("mixer_funded_pct"))
            if mixer is not None and mixer >= 20:
                bullets.append(f"{mixer:.0f}% of supply mixer-funded")
        # Deployer history.
        dep = evidence.get("deployer")
        if isinstance(dep, dict):
            label = str(dep.get("risk_label") or "").upper()
            total = dep.get("total_deployments")
            rug = dep.get("rug_count")
            if label in ("REPEAT_RUG", "FARM", "PROLIFIC", "CRITICAL", "HIGH"):
                bullets.append(f"Deployer history: {label}")
            elif (
                isinstance(total, (int, float))
                and float(total) >= 5
                and isinstance(rug, (int, float))
                and float(rug) > 0
            ):
                bullets.append(f"Deployer made {total} token(s), {rug} rug/dump")

    # ── Legacy scanner fields ──
    if data.get("bundle_detected"):
        bullets.append("Bundled launch detected (same-block buy cluster)")
    hp = data.get("honeypot")
    if hp is True or str(hp).lower() in ("true", "yes", "detected"):
        bullets.append("Honeypot detected — cannot sell after buy")
    buy_tax = _pct(data.get("buy_tax"))
    sell_tax = _pct(data.get("sell_tax"))
    if sell_tax is not None and sell_tax >= 20:
        bullets.append(f"Sell tax {sell_tax:.0f}% — exit is taxed/dissuaded")
    if buy_tax is not None and buy_tax >= 20:
        bullets.append(f"Buy tax {buy_tax:.0f}% — entry is taxed")
    if data.get("mintable") and not data.get("owner_renounced"):
        bullets.append("Supply is mintable and ownership not renounced")
    lp_locked = data.get("lp_locked")
    if lp_locked is False or str(lp_locked).lower() in ("no", "false", "unlocked"):
        bullets.append("LP not locked — liquidity can be pulled at any time")
    fresh = _pct(data.get("fresh_wallet_pct"))
    if fresh is not None and fresh >= 40:
        bullets.append(f"{fresh:.0f}% of supply in fresh wallets")

    return bullets


async def build_scanner_evidence(token: str, chain: str) -> dict[str, Any]:
    """Run ``token`` through the scanner; return a compact evidence dict.

    Returns ``{"scanned", "risk_level", "safety_score", "bullets", "note"}``
    or ``{}`` (no address / scanner unavailable / no defensible finding).
    Fail-open by contract — a scanner outage never blocks the campaign alert.
    """
    if not SHILL_SCANNER_EVIDENCE_ENABLED or not _looks_like_address(token):
        return {}
    try:
        from rugmunchbot.scanner import scan_token, scan_token_v2

        data = await scan_token_v2(token, chain)
        if data is None:
            data = await scan_token(token, chain)
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_watch:scanner_evidence_failed err=%s", str(exc)[:120])
        return {}
    if not isinstance(data, dict):
        return {}
    bullets = _evidence_bullets(data)
    risk_level = str(data.get("risk_level") or "").upper()
    if not bullets and risk_level not in ("HIGH", "CRITICAL"):
        # No defensible finding — don't attach a fabricated scare note.
        return {}
    note = " ".join(bullets[:4]) if bullets else f"Scanner verdict: {risk_level} risk."
    safety = data.get("safety_score")
    return {
        "scanned": True,
        "risk_level": risk_level or "unknown",
        "safety_score": round(float(safety), 1) if isinstance(safety, (int, float)) else None,
        "bullets": bullets,
        "note": note,
    }


def format_shill_alert(campaign: ShillCampaign) -> str:
    """HTML alert card for one campaign (Telegram parse_mode=html).

    2026-08-30: the card now explains WHAT is shilled, WHO is behind it, and
    WHY it is likely a scam (coordinated push = pre-arranged rug setup), and
    attaches the screenshot evidence (image path + extracted text) so the
    alert carries proof. ``campaign.importance`` (when scored) is rendered as
    a severity hint.
    """
    token = str(campaign.token)
    if campaign.chain == "ticker":
        head = f"🚨 <b>Coordinated shill: {token}</b>"
    else:
        short = token[:8] + "…" + token[-6:] if len(token) > 16 else token
        head = f"🚨 <b>Coordinated shill: <code>{short}</code></b> ({campaign.chain})"
    lines = [
        head,
        "",
        f"<b>What:</b> {token} pushed by <b>{len(campaign.accounts)} distinct accounts</b> in the last {SHILL_WINDOW_HOURS}h.",
        f"<b>Who:</b> {_esc(campaign.accounts[0])}"
        + (f" + {len(campaign.accounts) - 1} more" if len(campaign.accounts) > 1 else ""),
        "",
    ]
    for channel in campaign.accounts[:6]:
        lines.append(f"  • {_esc(channel)}")
    if len(campaign.accounts) > 6:
        lines.append(f"  …and {len(campaign.accounts) - 6} more")
    lines.extend(
        [
            "",
            "<b>Why this is likely a scam:</b> the same token promoted by many "
            "independent accounts inside one short window is the signature of an "
            "organised pump — the 'promoters' are the exit liquidity. Historically "
            "these campaigns precede a dump the moment the bag-holders arrive.",
        ]
    )
    # 2026-09-01 scanner evidence: when the campaign names a real token address
    # the canonical scanner ran it (build_scanner_evidence) — surface the
    # concrete on-chain findings (honeypot, deployer history, bundled supply,
    # LP risk) instead of relying on the coordination boilerplate alone.
    scanners = campaign.scanner_evidence or {}
    sc_bullets = scanners.get("bullets") or []
    if sc_bullets:
        lines.append("")
        lines.append("On-chain evidence (scanned):")
        for b in sc_bullets[:5]:
            lines.append(f"  • {_esc(b)}")
        if scanners.get("note") and len(sc_bullets) > 5:
            lines.append(f"  … {_esc(scanners['note'][:160])}")
    sample = campaign.mentions[:2]
    if sample:
        lines.append("")
        lines.append("Earliest pushes:")
        for m in sample:
            title = str(m.get("title") or "")[:90]
            url = str(m.get("url") or "")
            if url:
                lines.append(f'  → <a href="{_esc(url)}">{_esc(title)}</a>')
            else:
                lines.append(f"  → {_esc(title)}")
    if campaign.screenshots:
        lines.append("")
        lines.append("Evidence (screenshot capture):")
        for shot in campaign.screenshots[:3]:
            sx = _x_status_url(str(shot.get("url") or "")) or str(shot.get("url") or "")
            txt = str(shot.get("text") or "")[:140].replace("\n", " ")
            src = str(shot.get("account") or shot.get("image_path") or "")
            if txt:
                lines.append(f"  • {_esc(src)}: {_esc(txt)}")
            if sx:
                lines.append(f'    <a href="{_esc(sx)}">{_esc(sx)}</a>')
    lines.append("")
    lines.append("Coordinated shill = high rug-pump risk. Verify before ape: /scan @rugmunchbot")
    return "\n".join(lines)


# ── focused sweep + runner ────────────────────────────────────────────────


async def _sweep_shill_sources(
    client: Any | None = None, lookback_hours: int | None = None
) -> list[Any]:
    """Read recent messages from the focused KOL/calls channel list (bounded).

    Returns ``NewsArticle`` objects (fail-open ``[]``). Uses the same
    news_ingest read/normalize path as the main sweep but ONLY the
    ``SHILL_WATCH_CHANNELS`` handles — the full 200-channel discovery sweep
    is left to the tg_news job, so the shill watcher stays cheap.
    """
    from rugmunchbot._vendor.news_ingest.ingest import read_channel_messages
    from rugmunchbot._vendor.news_ingest.normalize import channel_message_to_article
    from rugmunchbot.channels.telegram_news._sources._fetch import (
        _build_headless_client,
        _inside_window,
    )

    if client is None:
        try:
            client = await _build_headless_client()
        except Exception as exc:  # fail-open by contract
            logger.warning("shill_watch:no_client err=%s", str(exc)[:120])
            return []
    if client is None:
        logger.warning("shill_watch:sweep_skipped_no_client")
        return []

    lookback = max(1, lookback_hours or SHILL_SWEEP_HOURS)
    cutoff = datetime.now(UTC) - timedelta(hours=lookback)
    articles: list[Any] = []
    for username in SHILL_WATCH_CHANNELS:
        try:
            messages = await read_channel_messages(client, username, SHILL_PER_CHANNEL_LIMIT)
        except Exception as exc:  # a dead channel never blocks the sweep
            logger.debug("shill_watch:channel_failed channel=%s err=%s", username, str(exc)[:80])
            continue
        for message in messages:
            try:
                if not _inside_window(message, cutoff):
                    continue
                article = channel_message_to_article(message, "alpha")
            except Exception as exc:  # malformed row — skip, never raise
                logger.debug("shill_watch:bad_row channel=%s err=%s", username, str(exc)[:80])
                continue
            if article is not None:
                articles.append(article)
    return articles


async def run_shill_watch(ctx: Any = None) -> int:
    """Sweep KOL channels → detect campaigns → score → capture → route alert.

    Returns the number of alerts posted (0..``SHILL_MAX_ALERTS``). Dedupe is
    per token per calendar day (global ``_remember`` namespace), so a
    campaign alerts once a day, never every tick. Fail-open at every stage.

    2026-08-30 (intelligent warning): each campaign is rerank-scored
    (``channels.importance`` via the qwen3-reranker lane) and routed by
    importance rather than posted unconditionally:

      * importance >= ``ROUTE_MAIN_MIN_IMPORTANCE`` (0.7) → @cryptorugmuncher
        AND @rmialerts (a severe, actively-promoted rug setup) — mirrored to
        the feed best-effort;
      * importance >= ``ROUTE_NEWS_MIN_IMPORTANCE`` (0.4) → @rmialerts only
        (danger intel, never the news channel);
      * below the news floor → held (no spam).

    Every detected campaign also triggers a cheap X screenshot capture
    (``tools.shill_shot`` — one DeepInfra VL-4B vision call per shill mention,
    bounded) and the screenshot reference + OCR text is persisted to the DB
    (``shill_evidence``) and the Qdrant ``threat_intel_v2`` collection with
    the source link, so the evidence survives a deleted tweet.
    """
    from rugmunchbot.channels.router import (
        ROUTE_MAIN_MIN_IMPORTANCE,
        ROUTE_NEWS_MIN_IMPORTANCE,
    )
    from rugmunchbot.channels.scheduler._core import _chat_id, _release, _remember, _safe_send

    alerts_chat = _chat_id("alerts")
    main_chat = _chat_id("main")
    if not alerts_chat and not main_chat:
        logger.debug("shill_watch:skipped_no_alert_or_main_chat")
        return 0

    articles = await _sweep_shill_sources()
    if not articles:
        logger.debug("shill_watch:no_fresh_articles")
        return 0

    campaigns = detect_shill_campaigns(articles)
    if not campaigns:
        logger.info("shill_watch:no_campaigns articles=%s", len(articles))
        return 0

    # Score every campaign by rerank importance (fail-open → 0.0 → held).
    for campaign in campaigns[:SHILL_MAX_ALERTS]:
        await _score_shill_campaign(campaign)
    # Capture cheap X screenshots for evidence (one VL-4B vision call per shill).
    if SHILL_SCREENSHOT_ENABLED:
        remaining = max(0, SHILL_SCREENSHOT_BUDGET)
        for campaign in campaigns[:SHILL_MAX_ALERTS]:
            if remaining <= 0:
                break
            per_campaign = min(SHILL_SCREENSHOT_PER_CAMPAIGN, remaining)
            budget = min(per_campaign, len(campaign.mentions))
            captured = await _capture_campaign_shills(campaign, budget)
            remaining -= captured
    # Scanner evidence (2026-09-01): a campaign naming a real token address
    # is run through the canonical scanner so the alert carries concrete
    # on-chain "why this is likely a scam" findings (honeypot, deployer
    # history, bundled supply, LP risk). Fail-open → {} on any failure.
    for campaign in campaigns[:SHILL_MAX_ALERTS]:
        if not campaign.scanner_evidence and _looks_like_address(campaign.token):
            campaign.scanner_evidence = await build_scanner_evidence(campaign.token, campaign.chain)

    today = datetime.now(UTC).strftime("%Y%m%d")
    posted = 0
    for campaign in campaigns[:SHILL_MAX_ALERTS]:
        dedupe_key = f"shill:{campaign.key}:{today}"
        if not _remember("shill_watch", dedupe_key):
            continue
        if ctx is None:
            # No bot handle (manual/cron use) — release and skip.
            _release("shill_watch", dedupe_key)
            continue

        text = format_shill_alert(campaign)
        routed = False
        # Danger intel: never the news channel. Main only at/above the gate.
        if campaign.importance >= ROUTE_MAIN_MIN_IMPORTANCE and main_chat:
            routed = await _safe_send(ctx.bot, main_chat, text) or routed
            # W30 top-of-food-chain mirror: a shill campaign that escalates
            # to main also lands on feed.rugmunch.io (same content, same
            # order, source link preserved). Best-effort, never blocks.
            if routed:
                try:
                    from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed

                    _shill_ref = str(
                        (campaign.mentions[0].get("url") if campaign.mentions else "")
                        or campaign.token
                        or ""
                    )
                    mirror_post_to_feed(
                        {
                            "text": str(text),
                            "story_ref": _shill_ref,
                            "source": "shill_watch",
                            "kind": "shill_watch",
                        }
                    )
                except Exception as exc:
                    logger.warning("shill_feed_mirror_failed err=%s", str(exc)[:120])
        if campaign.importance >= ROUTE_NEWS_MIN_IMPORTANCE and alerts_chat:
            routed = await _safe_send(ctx.bot, alerts_chat, text) or routed
        if not routed:
            # Held below the floor (or every send failed) — release to retry.
            _release("shill_watch", dedupe_key)
            logger.info(
                "shill_watch:held token=%s importance=%s accounts=%s",
                campaign.token[:40],
                campaign.importance,
                len(campaign.accounts),
            )
            continue
        posted += 1
        logger.info(
            "shill_watch:alerted token=%s importance=%s accounts=%s mentions=%s shots=%s",
            campaign.token[:40],
            campaign.importance,
            len(campaign.accounts),
            len(campaign.mentions),
            len(campaign.screenshots),
        )
    logger.info(
        "shill_watch:run_done articles=%s campaigns=%s posted=%s",
        len(articles),
        len(campaigns),
        posted,
    )
    return posted


__all__ = [
    "SHILL_INTERVAL_MIN",
    "SHILL_MAX_ALERTS",
    "SHILL_MIN_ACCOUNTS",
    "SHILL_MIN_MENTIONS",
    "SHILL_SCANNER_EVIDENCE_ENABLED",
    "SHILL_SCREENSHOT_BUDGET",
    "SHILL_SCREENSHOT_DIR",
    "SHILL_SCREENSHOT_ENABLED",
    "SHILL_SCREENSHOT_PER_CAMPAIGN",
    "SHILL_SWEEP_HOURS",
    "SHILL_WATCH_CHANNELS",
    "SHILL_WATCH_ENABLED",
    "SHILL_WINDOW_HOURS",
    "ShillCampaign",
    "build_scanner_evidence",
    "detect_shill_campaigns",
    "format_shill_alert",
    "run_shill_watch",
]
