"""Telegram news-channel source config, categorization, and sweep.

Submodule of ``channels/telegram_news.py`` — owns the **fetch/registry** half
of the pipeline: the env-overridable channel list (explicit + default), the
followed-channel categorization (news / alpha / scam / other), the fail-open
headless-client bootstrap, the lookback time filter, and the bounded
``read → normalize → time-filter → dedupe`` sweep itself.

Public names are re-exported through ``channels/telegram_news.py`` (the shim);
module-level state that tests patch (``TG_NEWS_CHANNELS``,
``TG_NEWS_AUTO_DISCOVER``, ``TG_NEWS_MAX_CHANNELS``, ``_build_headless_client``)
is read through the shim at call time so ``patch.object(tg_news, ...)`` keeps
working — see the package docstring.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from app.domains.news_ingest.discovery import discover_followed_channels
from app.domains.news_ingest.ingest import (
    DEFAULT_DEDUP_COLLECTION,
    dedupe_articles,
    extract_canonical_url,
    filter_seen_articles,
    is_channel_cooldown,
    read_channel_messages,
)
from app.domains.news_ingest.normalize import channel_message_to_article, is_telegram_link
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from app.domains.news_ingest.models import ChannelMessage, NewsArticle
    from app.domains.telegram.rugmunchbot.channels import telegram_news as _tg
else:
    _tg = sys.modules[__package__ or ""]

logger = get_logger("rmi_bot.channels.tg_news")

# ── Channel list (env-overridable, no secrets) ──────────────────────────────

#: Default public crypto-news channels — a curated high-signal set spanning
#: news outlets, security/exploit alerts, and on-chain/alpha feeds. All
#: handles were probed against the t.me public preview (2026-08-16) and exist;
#: ``View @`` = public channel (MTProto-readable by any account), ``Contact @``
#: = channel exists but preview is disabled (needs the sweep account to be a
#: member — flagged [REVIEW] below; auto-disable prunes it if not reachable).
#: The ones also listed in ``app/databus/tg_channels.json`` carry that
#: registry's confidence notes.
#:
#: Language note: ``PANewsOfficial`` posts mostly in Korean / Chinese and is
#: kept in the default list on purpose — the English-only language gate in
#: ``normalize.channel_message_to_article`` (Unicode-script detection, never
#: translation) drops its non-English posts at runtime while a future English
#: post on that channel still lands. Do not remove it unless the runtime
#: filter is replaced.
#:
#: Growth path to hundreds of sources (no code change needed):
#:   1. ``RMI_TG_NEWS_CHANNELS`` env — comma/pipe-separated @handles, appended
#:      by the deployment without touching this file;
#:   2. ``app/databus/tg_channels.json`` — the reusable channel registry
#:      (``upsert_channel()`` / ``tg_channel_verify`` probe);
#:   3. auto-discovery — the operator account's own followed/member public
#:      channels (``RMI_TG_NEWS_AUTO_DISCOVER``, default on) already add every
#:      channel that account is in, bounded by ``RMI_TG_NEWS_MAX_CHANNELS``.
#:
#: Dead/renamed handles that repeatedly failed to resolve ("No user has X as
#: username") are commented out below — they cost an MTProto round-trip on
#: every tick. Auto-disable in ``app.domains.news_ingest.ingest`` additionally
#: skips any channel (explicit or discovered) after 3 consecutive read
#: failures for a 24h cooldown, so a stale handle can never burn the sweep
#: indefinitely.
_DEFAULT_CHANNELS: tuple[str, ...] = (
    # ── News outlets ──────────────────────────────────────────────────────
    "cointelegraph",  # official CoinTelegraph (View)
    "coindesk",  # official CoinDesk (View)
    "cryptoslate",  # CryptoSlate (Contact — preview disabled)
    "WatcherGuru",  # Watcher Guru (View)
    "bitcoinmagazine",  # Bitcoin Magazine [REVIEW: Contact preview, needs membership]
    "PANewsOfficial",  # PANews [REVIEW: verify casing] — mostly KR/CN → runtime language filter
    "cryptopotato",  # CryptoPotato (View)
    "coingape",  # CoinGape (View)
    "U_Today",  # U.Today (View)
    "newsbtc",  # NewsBTC (View)
    "dailyhodl",  # The Daily Hodl (View)
    "zycrypto",  # ZyCrypto (View)
    "coinpedia",  # Coinpedia (View)
    "blockworks",  # Blockworks (View)
    "cryptobriefing",  # Crypto Briefing (View)
    "coinjournal",  # CoinJournal (View)
    "thedefiant",  # The Defiant [REVIEW: Contact preview, needs membership]
    "decryptmedia",  # Decrypt [REVIEW: Contact preview, needs membership]
    "ambcrypto",  # AMBCrypto [REVIEW: Contact preview, needs membership]
    "bitcoinist",  # Bitcoinist [REVIEW: Contact preview, needs membership]
    "trustnodes",  # Trustnodes [REVIEW: Contact preview, needs membership]
    # ── Security / exploit alerts ─────────────────────────────────────────
    "CertiKAlert",  # CertiK Alert (View)
    "PeckShieldAlert",  # PeckShield Alert (View)
    "rektnews",  # REKT News — exploit/incident intel (View)
    "ScamSniffer",  # ScamSniffer — phishing alerts (View)
    "zachxbt",  # ZachXBT — on-chain sleuth (View)
    "SlowMist",  # SlowMist — security team [REVIEW: Contact preview, needs membership]
    # ── On-chain / alpha ──────────────────────────────────────────────────
    "lookonchain",  # Lookonchain — on-chain analytics (View)
    "spotonchain",  # Spot On Chain — on-chain alerts (View)
    "whale_alert",  # Whale Alert — large-transaction alerts (View)
    "ArkhamIntel",  # Arkham — entity/alert intel [REVIEW: Contact preview, needs membership]
    "OnChainWizard",  # OnChain Wizard — alpha/on-chain [REVIEW: Contact preview, needs membership]
    "AltcoinDaily",  # Altcoin Daily — alpha/community [REVIEW: Contact preview, needs membership]
    # ── Expansion batch 2026-08-16 (t.me preview probe; all resolve) ──────
    # ── News outlets (batch) ──────────────────────────────────────────────
    "thebitcoinnews",  # The Bitcoin News (View)
    "altcoinbuzz",  # Altcoin Buzz — news/alpha (View)
    "cryptopanic",  # CryptoPanic — news aggregator [REVIEW: Contact preview, needs membership]
    "cryptonewscom",  # Cryptonews.com [REVIEW: Contact preview, needs membership]
    # ── Security / exploit alerts (batch) ─────────────────────────────────
    "BlockSecTeam",  # BlockSec — security alerts (View)
    "halborn",  # Halborn — security audits/alerts (View)
    "solidityfinance",  # Solidity Finance — audits + alerts (View)
    "Hacken",  # Hacken — security audits (View)
    "verichains",  # Verichains — security research (View)
    "trmlabs",  # TRM Labs — crypto risk/security (View)
    "EthereumSecurity",  # Ethereum Foundation security [REVIEW: Contact preview, needs membership]
    "FuzzLandAlert",  # FuzzLand — exploit/rugpull alerts [REVIEW: Contact preview, needs membership]
    "BeosinAlert",  # Beosin — security alerts [REVIEW: Contact preview, needs membership]
    "hypernative",  # Hypernative — exploit monitoring [REVIEW: Contact preview, needs membership]
    "openzeppelin",  # OpenZeppelin — security research [REVIEW: Contact preview, needs membership]
    # ── DeFi / protocol channels (batch) ──────────────────────────────────
    "arbitrum",  # Arbitrum Announcements Official (View)
    "avax",  # AVAX — Avalanche official (View)
    "zksync",  # ZKsync — official (View)
    "starknet",  # Starknet community (View)
    "aptos",  # Aptos — official (View)
    "lidofinance",  # Lido Finance — staking/defi (View)
    "chainlink",  # Chainlink — official (View)
    "Uniswap",  # Uniswap — official [REVIEW: Contact preview, needs membership]
    "Aave",  # Aave — official [REVIEW: Contact preview, needs membership]
    "base",  # Base — official [REVIEW: Contact preview, needs membership]
    "solana",  # Solana — official [REVIEW: Contact preview, needs membership]
    # ── On-chain / alpha (batch) ──────────────────────────────────────────
    "glassnode",  # Glassnode — on-chain analytics (View)
    "cryptowhaletrades",  # Crypto Whale Trading — whale/on-chain (View)
    "icodrops",  # ICO Drops — token launch/alpha (View)
    "nansen_ai",  # Nansen — on-chain analytics [REVIEW: Contact preview, needs membership]
    "chainfeeds",  # ChainFeeds — crypto research digest [REVIEW: Contact preview, needs membership]
    # ── Known-dead handles (2026-08-16 MTProto probe: "No user has X as username") ──
    # "wublockchain",  # Wu Blockchain — DEAD handle
    # "TheBlock__",  # The Block — DEAD handle
    # "beincrypto",  # BeInCrypto — DEAD handle
    # "cryptonews",  # Cryptonews.com — DEAD handle
)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return list(default)
    parts = [p.strip().lstrip("@") for p in re_split(raw)]
    return [p for p in parts if p]


def re_split(value: str) -> list[str]:
    """Split an env list on commas, pipes, or whitespace."""
    return [p for p in value.replace(",", "|").replace(" ", "|").split("|") if p]


TG_NEWS_ENABLED = _env_bool("RMI_TG_NEWS_CHANNEL_ENABLED", True)
TG_NEWS_INTERVAL_MINUTES = _env_int("RMI_CHANNEL_TG_NEWS_INTERVAL_MINUTES", 30)
TG_NEWS_SWEEP_HOURS = _env_int("RMI_TG_NEWS_SWEEP_HOURS", 6)
TG_NEWS_PER_CHANNEL_LIMIT = _env_int("RMI_TG_NEWS_PER_CHANNEL_LIMIT", 30)
TG_NEWS_MAX_CHANNELS = _env_int("RMI_TG_NEWS_MAX_CHANNELS", 40)
TG_NEWS_POST_LIMIT = _env_int("RMI_TG_NEWS_POST_LIMIT", 5)
TG_NEWS_PERSIST = _env_bool("RMI_TG_NEWS_PERSIST", True)
TG_NEWS_RAG = _env_bool("RMI_TG_NEWS_RAG", True)
TG_NEWS_AUTO_DISCOVER = _env_bool("RMI_TG_NEWS_AUTO_DISCOVER", True)
TG_NEWS_CHANNELS = _env_list("RMI_TG_NEWS_CHANNELS", list(_DEFAULT_CHANNELS))
#: Dereference t.me-only links to the real article URL during the sweep
#: (fail-open; disabled via ``RMI_NEWS_TME_RESOLVE_ENABLED=0``).
TME_RESOLVE_ENABLED = _env_bool("RMI_NEWS_TME_RESOLVE_ENABLED", True)


# ── Followed-channel categorization (news / alpha / scam / other) ────────────


# Own channels (the account follows its own news/alerts/alpha channels) must
# never be auto-classified as news/alpha — they are already curated digests.
_CHANNEL_OWN_KEYWORDS: tuple[str, ...] = ("rugmunch", "rugmunchbot", "cryptorugmunch")

#: Alpha/whale channel title signals — routed to the alpha channel.
_CHANNEL_ALPHA_KEYWORDS: tuple[str, ...] = (
    "alpha",
    "whale",
    "whales",
    "signal",
    "signals",
    "calls",
    "call",
    "gem",
    "gems",
    "100x",
    "1000x",
    "moon",
    "mooner",
    "plays",
    "insider",
    "sniper",
    "smart money",
    "accumulation",
    "early entries",
    "pump group",
    "pnd",
)

#: News/media/market channel title signals — routed to the news channel.
_CHANNEL_NEWS_KEYWORDS: tuple[str, ...] = (
    "news",
    "cryptonews",
    "crypto",
    "coin",
    "coins",
    "finance",
    "market",
    "markets",
    "headlines",
    "daily",
    "today",
    "update",
    "updates",
    "media",
    "journal",
    "wire",
    "insight",
    "insights",
    "briefing",
    "report",
    "bulletin",
    "digest",
    "magazine",
    "times",
    "cointelegraph",
    "coindesk",
    "decrypt",
    "block",
    "bitcoin",
    "ethereum",
    "solana",
    "altcoin",
    "macro",
    "trading",
    "slate",
    "guru",
    "watcher",
    # Security/exploit alert signals — discovered security firms and exploit
    # alert channels ("PeckShield Alert", "CertiK Alert", "BlockSec") would
    # otherwise fall through to ``other`` and be dropped from the sweep.
    "alert",
    "alerts",
    "security",
    "exploit",
    "exploits",
    "intel",
    "audit",
    "phishing",
    "breach",
)


def classify_channel_category(title: str, username: str) -> str:
    """Classify a followed channel by title/username heuristics.

    Returns one of ``news`` | ``alpha`` | ``scam`` | ``other``:

    * ``scam``  — reuses the existing scam/shill classification in
      ``channels/scam_watch.py`` (``_classify_dialog``) for **unambiguous**
      scam signals (pump/shill/airdrop/seed-phrase/rug/honeypot). Channels
      the scam sweep flags only as ``ALPHA_RUG_SHILL`` (alpha/calls/signals/
      gem/100x titles) are *not* excluded here — their call content routes to
      the alpha channel while scam_watch still scans their tokens
      independently. No keyword duplication.
    * ``alpha`` — whale/alpha/signal/calls-style titles → routed to the alpha
      channel.
    * ``news``  — news/media/market-style titles → routed to the news channel.
    * ``other`` — everything else (personal chats, groups, non-crypto) →
      excluded from the sweep entirely.

    Pure + fail-open (a broken scam_watch import never breaks categorization).
    """
    haystack = f"{title or ''} {username or ''}".lower()
    if any(word in haystack for word in _CHANNEL_OWN_KEYWORDS):
        return "other"
    try:
        from app.domains.telegram.rugmunchbot.channels.scam_watch import (
            ScamSourceType,
            _classify_dialog,
        )

        subtype = _classify_dialog(str(title or ""), str(username or ""))
        if subtype is not None and subtype != ScamSourceType.ALPHA_RUG_SHILL:
            return "scam"
    except Exception:  # fail-open by contract
        logger.debug("tg_news:scam_classify_unavailable", exc_info=True)
    if any(kw in haystack for kw in _CHANNEL_ALPHA_KEYWORDS):
        return "alpha"
    if any(kw in haystack for kw in _CHANNEL_NEWS_KEYWORDS):
        return "news"
    return "other"


# ── Source config ────────────────────────────────────────────────────────────


def channel_sources() -> list[dict[str, Any]]:
    """Source-config dicts (``username`` + ``category``) for the sweep.

    Returns the explicit ``RMI_TG_NEWS_CHANNELS`` list (falling back to
    ``_DEFAULT_CHANNELS``). This is the *fallback* list — when auto-discovery
    is enabled and the headless client is available,
    :func:`resolve_channel_sources` prefers the account's own channels.
    """
    return [{"username": u, "category": "crypto_news", "name": u} for u in _tg.TG_NEWS_CHANNELS]


async def resolve_channel_sources(client: Any | None = None) -> list[dict[str, Any]]:
    """Resolve the sweep's channel list, preferring the account's own channels.

    Priority (highest first):
      1. **Discovered** — the logged-in account's followed/member public
         channels, enumerated via Telethon ``get_dialogs()``
         (:func:`app.domains.news_ingest.discover_followed_channels`), gated
         by ``RMI_TG_NEWS_AUTO_DISCOVER`` (default on). Every discovered
         channel is categorized by :func:`classify_channel_category`:
         **news** → ``crypto_news`` and **alpha** → ``alpha`` are swept;
         **scam** channels are left to the scam_watch job (no duplication)
         and **other** channels (personal chats, non-crypto) are skipped.
         The result is bounded by ``RMI_TG_NEWS_MAX_CHANNELS``.
      2. **Explicit** — ``RMI_TG_NEWS_CHANNELS`` env list.
      3. **Default** — ``_DEFAULT_CHANNELS``.

    Fail-open: when auto-discovery is disabled, the client is missing, or
    discovery returns nothing, the explicit/default list is used.
    """
    if _tg.TG_NEWS_AUTO_DISCOVER and client is not None:
        try:
            discovered = await discover_followed_channels(client)
        except Exception as exc:  # discovery must never break the sweep
            logger.warning("tg_news:auto_discover_failed err=%s", str(exc)[:200])
            discovered = []
        if discovered:
            sources: list[dict[str, Any]] = []
            seen: set[str] = set()
            counts = {"news": 0, "alpha": 0, "scam": 0, "other": 0}
            for username, title in discovered:
                handle = str(username or "").strip().lstrip("@")
                if not handle or handle in seen:
                    continue
                seen.add(handle)
                category = classify_channel_category(str(title or ""), handle)
                counts[category] += 1
                if category in ("scam", "other"):
                    continue  # scam → scam_watch; other → not news-relevant
                category_tag = "crypto_news" if category == "news" else "alpha"
                sources.append(
                    {
                        "username": handle,
                        "category": category_tag,
                        "name": str(title or handle),
                        "channel_category": category,
                    }
                )
                if len(sources) >= _tg.TG_NEWS_MAX_CHANNELS:
                    break
            if sources:
                logger.info(
                    "tg_news:using_discovered_channels count=%s by_category=%s",
                    len(sources),
                    counts,
                )
                return sources
            logger.debug("tg_news:auto_discover_no_news_channels fallback_to_explicit counts=%s", counts)
    return channel_sources()


# ── Client bootstrap (fail-open) ─────────────────────────────────────────────


async def _build_headless_client() -> Any:
    """Return the singleton headless Telethon client, or ``None`` (never raises).

    When telethon is missing or the gopass API credentials are unpopulated the
    existing client raises; we log the exact setup needed and degrade.
    """
    try:
        from app.domains.telegram.headless import get_headless_client

        client = await get_headless_client()
        if getattr(client, "is_connected", False):
            return client
        return None
    except Exception as exc:  # fail-open by contract
        logger.warning("tg_news:headless_client_unavailable err=%s", str(exc)[:200])
        return None


# ── Time filter ──────────────────────────────────────────────────────────────


def _as_utc(value: datetime) -> datetime:
    """Normalize a (possibly naive) datetime to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _inside_window(message: ChannelMessage, cutoff: datetime) -> bool:
    """True when a message falls inside the lookback window.

    Messages without a date are kept (best-effort — cross-run dedup on the
    stable ``news_id`` still prevents re-ingesting ancient items).
    """
    posted = message.posted_at
    if posted is None:
        return True
    return _as_utc(posted) >= cutoff


# ── Story image (best-effort og:image fallback) ──────────────────────────────

_OG_IMAGE_TIMEOUT: float = 3.0
_OG_HTML_MAX_CHARS: int = 512_000

#: og:image / twitter:image meta extraction (attribute-order tolerant, case-
#: insensitive). Best-effort only — a page without a usable image yields none.
_OG_IMAGE_RES: Final = (
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']', re.IGNORECASE),
)


# ── Outbound fetch with proxy escalation (best available egress) ─────────────

#: FlareSolverr /v1 endpoint (env-overridable). The fleet's live instance is
#: the ``pry-flaresolverr`` container mapped to host ``127.0.0.1:8192``; the
#: old ``:8191`` mapping is dead (verified 2026-08-16 — 8191 unreachable,
#: 8192 answers ``sessions.list`` on FlareSolverr 3.5.0). Default matches
#: ``app.databus.proxy_scraper``.
FLARESOLVERR_URL: Final[str] = os.getenv("FLARESOLVERR_URL", "http://127.0.0.1:8192/v1").rstrip("/")
#: Hard timeout for one FlareSolverr request.get call.
_FLARE_TIMEOUT: float = 15.0

#: WAF-challenge markers on 200 responses (same heuristic as proxy_scraper).
_WAF_MARKERS: Final[tuple[str, ...]] = (
    "cf-chl-",
    "challenge-platform",
    "just a moment",
    "attention required",
    "enable javascript and cookies",
    "verify you are human",
    "checking your browser",
)
#: Status codes that trigger proxy escalation.
_ESCALATE_STATUS: Final[frozenset[int]] = frozenset({403, 429, 401, 503})


def _is_waf_block(status: int | None, body: str) -> bool:
    """True when the response looks like a WAF/challenge block (not real HTML)."""
    if status in _ESCALATE_STATUS:
        return True
    if status != 200:
        return False
    if len(body) > 50_000:
        return False  # a real page, not a challenge stub
    lowered = body.lower()
    return any(marker in lowered for marker in _WAF_MARKERS)


async def _fetch_via_flaresolverr(url: str) -> str | None:
    """Best-effort FlareSolverr fetch (fail-open → ``None``).

    POSTs the standard ``{"cmd": "request.get", ...}`` payload to
    ``FLARESOLVERR_URL`` and returns the solved HTML body capped at
    ``_OG_HTML_MAX_CHARS``. Never raises.
    """
    try:
        import httpx

        payload = {"cmd": "request.get", "url": url, "maxTimeout": int(_FLARE_TIMEOUT * 1000)}
        async with httpx.AsyncClient(
            timeout=_FLARE_TIMEOUT, follow_redirects=False, headers={"User-Agent": "rmi-news/1.0"}
        ) as client:
            response = await client.post(FLARESOLVERR_URL, json=payload)
            if not response.is_success:
                return None
            data = response.json()
        solution = data.get("solution") or {}
        body = solution.get("response")
        if isinstance(body, str) and body.strip():
            return body[:_OG_HTML_MAX_CHARS]
        return None
    except Exception:  # fail-open by contract
        logger.debug("tg_news:flare_fetch_failed url=%s", url[:120], exc_info=True)
        return None


async def _fetch_page(url: str, timeout: float) -> str | None:
    """Fetch *url*'s HTML, escalating to the best available egress.

    Ladder (fail-open, never raises):
      1. **Direct** httpx GET with a browser-ish UA, bounded by *timeout*.
      2. **Rotating residential pool** (``app.databus.proxy_rotator``) when
         ``WEBSHARE_PROXY_LIST`` is configured (gopass ``webshare_proxy_list``
         → env at deploy; absent → tier disabled → direct, fail-open). Retries
         once on the next proxy after a failure, then falls through.
      3. **FlareSolverr** at ``FLARESOLVERR_URL`` for WAF-blocked fetches —
         the live fleet instance on ``127.0.0.1:8192``.

    Returns the HTML body (capped at ``_OG_HTML_MAX_CHARS``) or ``None``.
    Used by both og:image extraction and t.me dereference so a
    Cloudflare-blocked publisher page still resolves without stalling the
    sweep.
    """
    body: str | None = None
    status: int | None = None
    try:
        import httpx

        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": "rmi-news/1.0"}
        ) as client:
            response = await client.get(url)
            status = int(response.status_code)
            if status == 200:
                body = response.text[:_OG_HTML_MAX_CHARS]
    except Exception:
        logger.debug("tg_news:direct_fetch_failed url=%s", url[:120], exc_info=True)

    if body is not None and not _is_waf_block(status, body):
        return body

    # 2) Rotating residential pool — only active when the deployment sets
    # WEBSHARE_PROXY_LIST (gopass webshare_proxy_list → env at deploy; absent
    # → rotator disabled → skip, direct egress preserved). Cascade per
    # app.databus.proxy_rotator: proxy → next proxy (once) → fall through to
    # FlareSolverr. Credentials are never logged.
    try:
        from app.databus.proxy_rotator import get_proxy_rotator

        rotator = get_proxy_rotator()
        if rotator.enabled:
            proxy = rotator.get_rotating_proxy()
            attempts = 0
            while proxy is not None and attempts < 2:
                attempts += 1
                client = rotator.get_client(proxy=proxy)
                response = await client.get(url, headers={"User-Agent": "rmi-news/1.0"})
                if response.is_success and not _is_waf_block(response.status_code, response.text[:_OG_HTML_MAX_CHARS]):
                    return response.text[:_OG_HTML_MAX_CHARS]
                proxy = rotator.next_after_failure(proxy)
    except Exception:
        logger.debug("tg_news:proxy_pool_fetch_failed url=%s", url[:120], exc_info=True)

    # 3) FlareSolverr for WAF-blocked fetches.
    return await _fetch_via_flaresolverr(url)


def _normalize_image_url(raw: str, base_url: str) -> str | None:
    """Resolve protocol-relative / root-relative og:image URLs to absolute."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("/"):
        from urllib.parse import urlsplit

        parts = urlsplit(base_url)
        return f"{parts.scheme}://{parts.netloc}{raw}"
    return raw


async def _fetch_og_image(url: str, timeout: float = _OG_IMAGE_TIMEOUT) -> str | None:
    """Best-effort og:image for a real article URL (fail-open → ``None``).

    Skips Telegram links (t.me pages have no meaningful story image) and
    never raises: network errors, timeouts, non-200s, and pages without an
    image meta tag all yield ``None``. Bound to *timeout* seconds for the
    direct attempt so a slow publisher never blocks the sweep; blocked pages
    escalate to the proxy ladder (FlareSolverr when needed).
    """
    if is_telegram_link(url):
        return None
    html = await _fetch_page(url, timeout)
    if not html:
        return None
    for pattern in _OG_IMAGE_RES:
        match = pattern.search(html)
        if match:
            return _normalize_image_url(match.group(1), url)
    return None


def _should_fetch_og_image(article: NewsArticle) -> bool:
    """True when the sweep should try the source page's og:image for *article*.

    Skips articles whose source URL is a t.me fallback (no meaningful story
    image) and articles that already carry a real media reference. The
    ``tg:`` marker that normalize sets for photo/video posts without a real
    media id is replaced when the source og:image resolves.
    """
    url = (article.source_url or "").strip()
    if not url or is_telegram_link(url):
        return False
    media = (article.media or "").strip()
    return not media or media.startswith("tg:")


# ── t.me link dereference (best-effort, env-gated) ──────────────────────────

#: Hard timeout for ONE t.me page fetch (fail-open → original URL).
_TME_RESOLVE_TIMEOUT: float = 5.0

#: t.me URL inside the message TEXT — the real signal that the message's only
#: link is a Telegram page (the synthesized ``source_url`` fallback is not a
#: link the channel posted; never dereference a bare fallback).
_TME_IN_TEXT_RE: Final = re.compile(r"https?://(?:t\.me|telegram\.me)/", re.IGNORECASE)

#: og:url / rel=canonical extraction (attribute-order tolerant, case-
#: insensitive), then the first external http(s) href. Best-effort only — a
#: t.me page without a usable real URL yields the original link.
_TME_OG_URL_RES: Final = (
    re.compile(r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']', re.IGNORECASE),
)
_TME_CANONICAL_RES: Final = (
    re.compile(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']', re.IGNORECASE),
)
_TME_HREF_RES: Final = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)


def _message_has_tme_link(article: NewsArticle) -> bool:
    """True when the message TEXT carries a t.me / telegram.me link.

    Only messages whose own text links to a Telegram page are candidates for
    dereferencing — a synthesized ``source_url`` fallback (created by
    normalize when the message had no link at all) is never dereferenced.
    """
    return bool(_TME_IN_TEXT_RE.search(f"{article.title or ''} {article.body or ''}"))


def _extract_tme_target(html: str, base_url: str) -> str | None:
    """First usable real-article URL inside a t.me page (pure, fail-open).

    Priority: og:url → rel=canonical → first external http(s) href. A
    candidate that is still a Telegram link (the page's own share URL) is
    skipped so the real article link wins.
    """
    for pattern in _TME_OG_URL_RES:
        match = pattern.search(html)
        if match:
            candidate = _normalize_image_url(match.group(1), base_url)
            if candidate and not is_telegram_link(candidate):
                return candidate
    for pattern in _TME_CANONICAL_RES:
        match = pattern.search(html)
        if match:
            candidate = _normalize_image_url(match.group(1), base_url)
            if candidate and not is_telegram_link(candidate):
                return candidate
    for match in _TME_HREF_RES.finditer(html):
        candidate = _normalize_image_url(match.group(1), base_url)
        if candidate and not is_telegram_link(candidate):
            return candidate
    return None


async def resolve_tme_link(url: str) -> str:
    """Dereference a t.me link to the real article URL (fail-open).

    Fetches the t.me page (direct, 5s timeout; WAF-blocked pages escalate to
    the proxy ladder — FlareSolverr / rotating pool when configured) and
    extracts the real article URL from ``og:url`` / ``rel=canonical`` / the
    first external http(s) link. Returns the resolved URL, or the original
    *url* unchanged on any failure (network error, timeout, non-200, page
    without a real link). Never raises — a slow publisher never blocks the
    sweep.
    """
    url = (url or "").strip()
    if not is_telegram_link(url):
        return url
    html = await _fetch_page(url, _TME_RESOLVE_TIMEOUT)
    if not html:
        return url
    return _extract_tme_target(html, url) or url


# ── Content enrichment (t.me deref + title rewrite, env-gated, fail-open) ──

#: Max concurrent LLM title rewrites per sweep (backend chat is CPU-bound).
_TITLE_REWRITE_CONCURRENCY: Final = 4
#: Cap on articles whose titles are rewritten per sweep — the sweep does not
#: know which stories the scheduler will post, so prewarm a bounded prefix
#: (newest-first read order) instead of hammering the LLM for every article.
_TITLE_REWRITE_MAX_ARTICLES: Final = 12


async def _enrich_articles(articles: list[NewsArticle]) -> None:
    """Best-effort content enrichment for the posted card (fail-open).

    1. **t.me dereference** (``RMI_NEWS_TME_RESOLVE_ENABLED``, default on):
       when a message's ONLY link is a t.me page, resolve the real article
       URL onto ``article.resolved_url`` so the posted card links readers to
       the original story instead of the channel page.
    2. **LLM title rewrite** (``RMI_NEWS_TITLE_REWRITE_ENABLED``, default
       off): humanize the headline onto ``article.post_title`` (bounded to
       ``_TITLE_REWRITE_MAX_ARTICLES``). ``article.title`` keeps the
       original headline so persistence is never changed.

    Never raises and never blocks the sweep: every sub-step fails open.
    """
    if not articles:
        return

    if _tg.TME_RESOLVE_ENABLED:
        tme_only = [
            a
            for a in articles
            if is_telegram_link(a.source_url) and not extract_canonical_url(a) and _message_has_tme_link(a)
        ]
        if tme_only:
            results = await asyncio.gather(
                *(resolve_tme_link(a.source_url) for a in tme_only),
                return_exceptions=True,
            )
            for article, resolved in zip(tme_only, results, strict=True):
                if isinstance(resolved, str) and resolved and not is_telegram_link(resolved):
                    article.resolved_url = resolved

    if _tg.TITLE_REWRITE_ENABLED:
        from app.domains.telegram.rugmunchbot.channels.rewrite_titles import rewrite_news_title

        sem = asyncio.Semaphore(_TITLE_REWRITE_CONCURRENCY)

        async def _rewrite_one(article: NewsArticle) -> None:
            if not (article.title or "").strip():
                return
            async with sem:
                rewritten = await rewrite_news_title(article.title)
            if rewritten and rewritten != article.title:
                article.post_title = rewritten

        await asyncio.gather(
            *(_rewrite_one(a) for a in articles[:_TITLE_REWRITE_MAX_ARTICLES]),
            return_exceptions=True,
        )


# ── Sweep: read → normalize → time-filter → dedupe ──────────────────────────


async def sweep_telegram_news(
    *,
    client: Any | None = None,
    lookback_hours: int | None = None,
    per_channel_limit: int | None = None,
) -> list[NewsArticle]:
    """Read recent messages from the configured channels.

    Returns deduped ``NewsArticle`` objects newer than ``lookback_hours``
    (default ``TG_NEWS_SWEEP_HOURS``). Accepts an injected client (any object
    exposing ``iter_messages`` or the news_ingest ``read_messages`` protocol —
    used by tests); when omitted the headless singleton is built (fail-open →
    ``[]``).

    The channel list is resolved by :func:`resolve_channel_sources`: the
    account's own followed/member channels (when ``RMI_TG_NEWS_AUTO_DISCOVER``
    is on and the client exposes ``get_dialogs``) are primary — categorized
    into news / alpha / scam / other (scam left to scam_watch, other skipped)
    and bounded by ``RMI_TG_NEWS_MAX_CHANNELS`` — and the explicit
    ``RMI_TG_NEWS_CHANNELS`` / ``_DEFAULT_CHANNELS`` list is the fallback.

    Dedup is two-layered: in-batch (content hash / source URL) then
    cross-run via the shared Redis content-hash sets
    (:func:`app.domains.news_ingest.ingest.filter_seen_articles`) so content
    seen in a previous sweep — or ingested by the RAG pipeline — is never
    re-read/re-embedded (Redis down → fail-open, everything treated as fresh).
    """
    if client is None:
        client = await _tg._build_headless_client()  # type: ignore[attr-defined]
    if client is None:
        logger.warning("tg_news:sweep_skipped_no_client")
        return []

    sources = await resolve_channel_sources(client)
    if not sources:
        logger.info("tg_news:no_sources_configured")
        return []

    lookback = max(1, lookback_hours or TG_NEWS_SWEEP_HOURS)
    per_channel = max(5, per_channel_limit or TG_NEWS_PER_CHANNEL_LIMIT)
    cutoff = datetime.now(UTC) - timedelta(hours=lookback)

    articles: list[NewsArticle] = []
    for source in sources:
        username = str(source.get("username") or "").strip().lstrip("@")
        if not username:
            continue
        if is_channel_cooldown(username):
            logger.debug("tg_news:channel_cooldown_skip channel=%s", username)
            continue
        category = str(source.get("category") or "crypto_news")
        messages = await read_channel_messages(client, username, per_channel)
        for message in messages:
            if not _inside_window(message, cutoff):
                continue
            article = channel_message_to_article(message, category)
            if article is not None:
                articles.append(article)
    articles = dedupe_articles(articles)
    articles, seen_skipped = filter_seen_articles(articles, DEFAULT_DEDUP_COLLECTION)
    if seen_skipped:
        logger.info("tg_news:cross_run_dedup skipped=%s kept=%s", seen_skipped, len(articles))
    # Best-effort story image: capture the source page's og:image for any
    # article without a real media reference (fail-open, never blocks).
    missing_images = [a for a in articles if _should_fetch_og_image(a)]
    if missing_images:
        results = await asyncio.gather(
            *(_fetch_og_image(a.source_url) for a in missing_images),
            return_exceptions=True,
        )
        for article, image in zip(missing_images, results, strict=True):
            if isinstance(image, str) and image.strip():
                article.media = image.strip()
    # Best-effort content enrichment for the posted card (env-gated,
    # fail-open): t.me-only links → real article URL; optional LLM title
    # rewrite. Never blocks the sweep.
    await _enrich_articles(articles)
    return articles
