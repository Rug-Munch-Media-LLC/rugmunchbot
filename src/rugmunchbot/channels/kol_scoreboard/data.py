"""KOL Scoreboard — data model, config, and data access.

Split from the former monolithic ``kol_scoreboard.py``: this module owns
the dataclasses (``KolCall`` / ``KolReportCard`` / ``KolIdentity``), the
env-driven config constants, and all data fetching (sweep, deletion
detection, card building, identity resolution, JSONL export).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.kol_scoreboard")

#: Master switch — new name first, legacy env kept as fallback.
KOL_SCOREBOARD_ENABLED = os.getenv(
    "RMI_KOL_SCOREBOARD_ENABLED", os.getenv("RMI_KOL_SHAME_ENABLED", "true")
).lower() in (
    "1",
    "true",
    "yes",
)
KOL_SCOREBOARD_INTERVAL_MIN = int(
    os.getenv("RMI_KOL_SCOREBOARD_INTERVAL_MIN", os.getenv("RMI_KOL_SHAME_INTERVAL_MIN", "90"))
)
KOL_SCOREBOARD_MAX_ALERTS = int(
    os.getenv("RMI_KOL_SCOREBOARD_MAX_ALERTS", os.getenv("RMI_KOL_SHAME_MAX_ALERTS", "2"))
)
#: Calls older than this are ignored for new-entry purposes (freshness gate).
FRESH_CALL_HOURS = int(os.getenv("RMI_KOL_SCOREBOARD_FRESH_HOURS", "72"))
#: Default tracked handles — when set, the sweep only covers these; when
#: empty the full seeded registry is swept round-robin (continual expansion).
KOL_TRACKED_HANDLES: tuple[str, ...] = tuple(
    h.strip().lstrip("@") for h in os.getenv("RMI_KOL_TRACKED_HANDLES", "").split(",") if h.strip()
)

#: Call-language needles (cheap heuristic; the reranker refines downstream).
_CALL_VERB_RE = re.compile(r"\b(buy|bought|long|call|entered|entry|position)\b", re.I)
_RUG_VERB_RE = re.compile(r"\b(rug|dump|scam|ponzi|exit|rekt)\b", re.I)
_PUMP_RE = re.compile(r"\b(pump|gem|100x|moon|mooner|ape|all[- ]?in|breakout|alpha|launch)\b", re.I)
_TICKER_RE = re.compile(r"\$([A-Z0-9]{2,12})\b")
_ADDRESS_RE = re.compile(r"\b(0x[a-fA-F0-9]{6,}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")


# ══════════════════════════════════════════════════════════════════════════
# DATA MODEL
# ══════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class KolCall:
    """One captured KOL call on a token."""

    handle: str
    tweet_id: str
    token_symbol: str = ""
    token_address: str = ""
    chain: str = ""
    call_type: str = "buy"
    tweet_text: str = ""
    posted_at: str = ""
    source_url: str = ""
    wallet_address: str = ""
    is_deleted: bool = False
    deleted_at: str = ""
    result: str = "unknown"  # win | loss | flat | unknown
    captured_at: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> KolCall:
        return cls(
            handle=str(row.get("handle") or ""),
            tweet_id=str(row.get("tweet_id") or ""),
            token_symbol=str(row.get("token_symbol") or ""),
            token_address=str(row.get("token_address") or ""),
            chain=str(row.get("chain") or ""),
            call_type=str(row.get("call_type") or "buy"),
            tweet_text=str(row.get("tweet_text") or ""),
            posted_at=str(row.get("posted_at") or ""),
            source_url=str(row.get("source_url") or ""),
            wallet_address=str(row.get("wallet_address") or ""),
            is_deleted=bool(row.get("is_deleted") or 0),
            deleted_at=str(row.get("deleted_at") or ""),
            result=str(row.get("result") or "unknown"),
            captured_at=str(row.get("captured_at") or ""),
        )


@dataclass(slots=True)
class KolReportCard:
    """Per-KOL aggregated report card (the scoreboard's core render unit)."""

    handle: str
    kol_id: str = ""
    name: str = ""
    category: str = ""
    scammer_group: str = ""
    total_calls: int = 0
    deleted_calls: int = 0
    shill_calls: int = 0
    scam_token_calls: int = 0
    wins: int = 0
    losses: int = 0
    tokens_mentioned: int = 0
    wallet_address: str = ""
    wallets: list[dict[str, str]] = None  # type: ignore[assignment]
    name_changes: int = 0
    is_repeat_scammer: bool = False
    score: float = 0.0
    grade: str = "N/A"

    @property
    def in_hall_of_shame(self) -> bool:
        """Hall of Shame = REPEAT scammers only (≥2 deletions / ≥2 scam calls
        / ≥3 shills) AND a score that clears the hall threshold."""
        return self.is_repeat_scammer and self.score >= 15.0

    @property
    def win_rate(self) -> float:
        """Wins / (wins + losses); 0 when no resolved outcomes."""
        decided = self.wins + self.losses
        return round(self.wins / decided, 3) if decided else 0.0


@dataclass(slots=True)
class KolIdentity:
    """Canonical KOL identity — current + past handles resolve to this."""

    kol_id: str
    name: str = ""
    screen_name: str = ""
    category: str = ""
    source: str = ""
    verified: bool = False
    risk_level: str = "watch"
    wallets: list[str] = None  # type: ignore[assignment]
    scammer_group: str = ""
    notes: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any] | None) -> KolIdentity | None:
        if not row:
            return None
        return cls(
            kol_id=str(row.get("kol_id") or ""),
            name=str(row.get("name") or ""),
            screen_name=str(row.get("screen_name") or ""),
            category=str(row.get("category") or ""),
            source=str(row.get("source") or ""),
            verified=bool(row.get("verified") or 0),
            risk_level=str(row.get("risk_level") or "watch"),
            wallets=_json_list(row.get("wallets")),
            scammer_group=str(row.get("scammer_group") or ""),
            notes=str(row.get("notes") or ""),
        )


def _json_list(value: Any) -> list[str]:
    import json

    if isinstance(value, list):
        return [str(v.get("address") if isinstance(v, dict) else v) for v in value]
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
        return (
            [str(v.get("address") if isinstance(v, dict) else v) for v in parsed]
            if isinstance(parsed, list)
            else []
        )
    except (ValueError, TypeError):
        return []


# ══════════════════════════════════════════════════════════════════════════
# IDENTITY RESOLUTION + NAME-CHANGE DETECTION (100% correct identification)
# ══════════════════════════════════════════════════════════════════════════


def resolve_identity(handle: str) -> KolIdentity | None:
    """Resolve ANY handle (current or past) to the canonical KOL identity.

    Backed by ``db.kol.resolve_handle`` (alias table → identity). This is
    the core of 100%-correct identification: a KOL who renames (e.g.
    altcoingordon → <new handle>) keeps their old handle as an alias, so
    both names land on the same report card. Fail-open ``None``.
    """
    try:
        from rugmunchbot.db import resolve_handle

        return KolIdentity.from_row(resolve_handle(handle))
    except Exception:  # fail-open by contract
        return None


def detect_name_change(handle: str, payload_screen_name: str) -> bool:
    """Record a screen-name transition when the payload differs from swept.

    ``handle`` is what we swept; ``payload_screen_name`` is what the X API
    actually returned. When they differ (and the payload name is real), the
    KOL renamed: record ``kol_name_changes``, demote the old alias, register
    the new alias as current. Returns True when a change was recorded.
    Fail-open False.
    """
    payload = str(payload_screen_name or "").strip().lstrip("@")
    swept = str(handle or "").strip().lstrip("@")
    if not payload or not swept or payload.lower() == swept.lower():
        return False
    try:
        from rugmunchbot.db import (
            demote_alias,
            record_kol_name_change,
            register_alias,
            resolve_handle,
        )

        identity = resolve_handle(swept)
        if identity is None:
            return False
        kol_id = str(identity.get("kol_id") or swept.lower())
        record_kol_name_change(kol_id, from_handle=swept, to_handle=payload)
        demote_alias(swept)
        register_alias(kol_id, payload, is_current=True)
        logger.warning("kol_scoreboard:name_change kol_id=%s from=%s to=%s", kol_id, swept, payload)
        return True
    except Exception:  # fail-open by contract
        return False


def link_wallet_from_call(call: KolCall) -> bool:
    """Attach a wallet-shaped address from a call tweet to the KOL's KB.

    Inference-based wallet linking: when a KOL's own tweet contains a
    long base58/0x address, it is a strong signal the address belongs to
    them (or at least to their wallet knowledgebase). The chain is derived
    from the address shape (0x → ethereum, base58 → solana); confidence is
    ``inference`` — the weakest tier, below deterministic/strong_heuristic/
    probabilistic from the curated KB. Returns True when a new wallet was
    attached. Fail-open False.
    """
    from rugmunchbot.channels.kol_scoreboard.scoring import extract_wallet_address

    address = extract_wallet_address(call.tweet_text)
    if not address:
        return False
    chain = "ethereum" if address.startswith("0x") else "solana"
    try:
        from rugmunchbot.db import attach_kol_wallet, resolve_handle

        identity = resolve_handle(call.handle)
        kol_id = (
            str(identity.get("kol_id") or call.handle.lower()) if identity else call.handle.lower()
        )
        return bool(
            attach_kol_wallet(
                kol_id,
                address,
                chain=chain,
                source="inference",
                evidence_url=call.source_url,
                confidence="inference",
            )
        )
    except Exception:  # fail-open by contract
        return False


# ══════════════════════════════════════════════════════════════════════════
# SWEEP (async I/O; store + bot are injected)
# ══════════════════════════════════════════════════════════════════════════


def _call_from_tweet(tweet: dict[str, Any]) -> KolCall | None:
    """Build a KolCall from a normalized ApiPass tweet (None if not a call)."""
    from rugmunchbot.channels.kol_scoreboard.scoring import classify_call, extract_tokens

    text = str(tweet.get("text") or "")
    handle = str(tweet.get("handle") or "")
    tweet_id = str(tweet.get("tweet_id") or "")
    if not text or not handle or not tweet_id:
        return None
    ctype = classify_call(text)
    if ctype == "other":
        return None
    ticker, addr = extract_tokens(text)
    return KolCall(
        handle=handle,
        tweet_id=tweet_id,
        token_symbol=ticker,
        token_address=addr,
        chain="ethereum" if addr and addr.startswith("0x") else "solana" if addr else "",
        call_type=ctype,
        tweet_text=text[:500],
        posted_at=str(tweet.get("created_at") or ""),
        source_url=str(tweet.get("url") or ""),
    )


def _sweep_pool() -> list[str]:
    """Handles to sweep this rotation: env override, else full registry."""
    if KOL_TRACKED_HANDLES:
        return list(KOL_TRACKED_HANDLES)
    try:
        from rugmunchbot.db import list_kol_identities

        return [
            str(r["screen_name"]) for r in list_kol_identities(limit=2000) if r.get("screen_name")
        ]
    except Exception:  # fail-open — fall back to the seed rows
        try:
            from rugmunchbot.kol_registry_seed import KOL_SEED

            return [r[0] for r in KOL_SEED]
        except Exception:
            return []


async def sweep_tracked_kol_tweets() -> list[KolCall]:
    """Fetch recent tweets from one tracked KOL via ApiPass (budget-bounded).

    Round-robin over the sweep pool — one handle per tick — so no tick
    exceeds the ≤5 task-creations/hr burn. Fail-open ``[]``.
    """
    try:
        from rugmunchbot.tools.apipass_x import fetch_user_tweets
    except Exception:  # pragma: no cover — import failure fails open
        logger.warning("kol_scoreboard:apipass_import_failed")
        return []

    pool = _sweep_pool()
    if not pool:
        return []
    offset = int(os.getenv("RMI_KOL_TRACKED_HANDLE_INDEX", "0"))
    handle = pool[offset % len(pool)]
    try:
        tweets = await fetch_user_tweets(handle, limit=10)
    except Exception as exc:  # fail-open by contract
        logger.debug("kol_scoreboard:sweep_failed handle=%s err=%s", handle, str(exc)[:120])
        return []

    # Name-change detection: the payload screen_name may differ from swept.
    for tweet in tweets[:1]:
        payload_handle = str(tweet.get("handle") or "")
        if payload_handle:
            detect_name_change(handle, payload_handle)

    calls: list[KolCall] = []
    for tweet in tweets:
        call = _call_from_tweet(tweet)
        if call is not None:
            calls.append(call)
    logger.info(
        "kol_scoreboard:swept handle=%s tweets=%s calls=%s",
        handle,
        len(tweets),
        len(calls),
    )
    return calls


async def detect_deleted_calls(
    calls: list[KolCall], window_hours: int = FRESH_CALL_HOURS
) -> list[KolCall]:
    """Re-check this window's recorded calls; mark the ones now gone.

    A call whose tweet now 404s on ApiPass (tweet-detail → None) is
    retracted. Bounded detail checks per tick (3 max). Calls with synthetic
    ``rug-`` storage keys (documented rug-history evidence, no live tweet)
    are skipped — they cannot 404 as tweets. Fail-open ``[]``.
    """
    try:
        from rugmunchbot.tools.apipass_x import fetch_tweet_detail
    except Exception:
        return []

    deleted: list[KolCall] = []
    for call in calls[:3]:
        if str(call.tweet_id or "").startswith("rug-"):
            continue  # synthetic evidence key, not a live tweet
        detail = await fetch_tweet_detail(call.tweet_id)
        if detail is None:
            deleted.append(call)
    return deleted


def _card_from_rows(
    calls: list[KolCall],
    cached: dict[str, dict[str, Any]],
    identity: KolIdentity | None = None,
    name_changes: int = 0,
) -> KolReportCard | None:
    """Build one card from fresh rows + a cached aggregate (pure wrapper)."""
    from rugmunchbot.channels.kol_scoreboard.scoring import compute_report_card

    if not calls:
        return None
    handle = calls[0].handle
    wallet = ""
    for c in calls:
        if c.wallet_address:
            wallet = c.wallet_address
            break
    if not wallet and identity and identity.wallets:
        wallet = identity.wallets[0]
    if not wallet:
        cached_row = cached.get(handle.lower())
        if cached_row:
            wallet = str(cached_row.get("wallet_address") or "")
    traced_wallets: list[dict[str, str]] = []
    if identity:
        try:
            from rugmunchbot.db import list_kol_wallets

            traced_wallets = list_kol_wallets(identity.kol_id)
        except Exception:  # fail-open by contract
            traced_wallets = []
    return compute_report_card(
        calls,
        wallet_address=wallet,
        name_changes=name_changes,
        identity=identity,
        traced_wallets=traced_wallets,
    )


def build_scoreboard_cards(limit: int = 400) -> list[KolReportCard]:
    """Build report cards for every KOL with recorded calls (fail-open).

    Reads the call store directly (grouped by handle), resolves each handle
    to its canonical identity (so renamed KOLs get one card), counts name
    changes, and computes the card. This is the render path for the
    scoreboard post — no cached-scores dependency. Fail-open ``[]``.
    """
    from collections import defaultdict

    from rugmunchbot.channels.kol_scoreboard.scoring import compute_report_card

    try:
        from rugmunchbot.db import list_kol_calls, list_kol_name_changes, resolve_handle
    except Exception:
        return []

    grouped: dict[str, list[KolCall]] = defaultdict(list)
    for row in list_kol_calls(limit=5000):
        grouped[str(row.get("handle") or "")].append(KolCall.from_row(row))

    cards: list[KolReportCard] = []
    for handle, rows in grouped.items():
        identity = KolIdentity.from_row(resolve_handle(handle))
        kol_id = identity.kol_id if identity else handle.lower()
        name_changes = len(list_kol_name_changes(kol_id=kol_id))
        traced_wallets: list[dict[str, str]] = []
        if identity:
            try:
                from rugmunchbot.db import list_kol_wallets

                traced_wallets = list_kol_wallets(identity.kol_id)
            except Exception:  # fail-open by contract
                traced_wallets = []
        card = compute_report_card(
            rows, identity=identity, name_changes=name_changes, traced_wallets=traced_wallets
        )
        cards.append(card)
        if len(cards) >= max(1, limit):
            break
    return cards


def _captured_within(call: KolCall, hours: float) -> bool:
    """True when a call was captured within the last *hours*."""
    try:
        ts = datetime.fromisoformat(call.captured_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return datetime.now(UTC) - ts.astimezone(UTC) <= timedelta(hours=max(1, hours))
    except (ValueError, TypeError):
        return True  # undated rows stay in the window


def _export_site_bridge() -> None:
    """Write the JSONL export so the site's ``/api/v1/kol/scammer-calls`` can render.

    Default path is the bot's own data dir; an operator pointing
    ``RMI_KOL_CALL_STORE_PATH`` at the backend's
    ``app/databus/scammer_calls.json`` wires the scoreboard directly onto
    the site. Fail-open.
    """
    try:
        from rugmunchbot.db import export_kol_calls_jsonl

        path = os.getenv(
            "RMI_KOL_CALL_STORE_PATH",
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "data",
                "kol_scoreboard.jsonl",
            ),
        )
        export_kol_calls_jsonl(path)
    except Exception as exc:  # fail-open by contract
        logger.debug("kol_scoreboard:export_failed err=%s", str(exc)[:120])
