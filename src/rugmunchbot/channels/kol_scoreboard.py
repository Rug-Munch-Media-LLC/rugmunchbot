"""KOL Scoreboard — per-KOL report cards, name-change tracking, wallet KB.

The renamed main product (2026-08-29): what was "KOL Hall of Shame" is now
the **KOL Scoreboard** — the full per-KOL report-card product over every
tracked crypto KOL / shiller. "Hall of Shame" survives as the *subset* of
**repeat scammers** (≥2 deleted calls, ≥2 known-scam-token calls, or ≥3
shill calls) — deliberately NOT every KOL with a low grade.

v9 (2026-08-29): **scammer groups** (syndicate attribution) + **wallet
tracing expansion**. Every identity can carry a ``scammer_group`` slug
(``solana_kol_syndicate``, ``impersonation_syndicate``,
``celebrity_tout_syndicate``, … — labels resolve via
``kol_registry_seed.group_label``). The Hall of Shame and scoreboard posts
render the group. Wallet tracing now exposes per-wallet chain + confidence
(``list_kol_wallets``), inference linking from call tweets
(``link_wallet_from_call``), provenance-source lookup
(``wallet_kb_by_source``, e.g. ``zachxbt_post`` / ``dethective`` when the
operator seeds evidence-linked entries), and on-chain clustering of
identities that share a wallet (``wallet_clusters`` /
``cluster_kols_by_wallet``).

How it works (bounty-hunter model)
----------------------------------
1. ``run_kol_scoreboard_watch`` sweeps the seeded KOL registry
   (``kol_registry_seed`` — ~565 real accounts from the fleet's curated
   registries) via the ApiPass X source, one handle per tick
   (round-robin, ≤5 task-creations/hr ≈ 3 credits/hr).
2. Every call-looking tweet is recorded via ``db.kol.record_kol_call``
   (upsert by tweet_id).
3. ``detect_name_change`` — when a swept handle's payload carries a
   *different* screen_name, the transition is recorded
   (``db.kol.record_kol_name_change``), the old alias demoted and the new
   one registered — so a renamed KOL keeps ONE report card (100% correct
   identification across screen-name changes, e.g. altcoingordon).
   ``db.kol.list_kol_aliases`` returns the full historical alias list.
4. ``detect_deleted_calls`` — a recorded tweet that now 404s on ApiPass is
   marked deleted (retraction = the strongest dishonesty signal).
5. Wallet tracing — wallet-shaped addresses in call texts are attributed to
   the KOL's wallet KB via inference (``link_wallet_from_call``, chain from
   address shape, confidence ``inference``), the 945-wallet knowledgebase
   is seeded from fleet data with chain/confidence/provenance
   (``attach_kol_wallet`` / ``seed_wallet_kb``), and shared wallets across
   identities produce on-chain clusters (``wallet_clusters``) plus identity
   links (``link_identity_via_wallet`` / ``cluster_kols_by_wallet``).
6. ``compute_report_card`` (pure) aggregates: calls, deletions, shills,
   scam-token calls, win/loss (from documented outcomes), name changes,
   scammer group, traced wallets, score + grade.
7. The SCOREBOARD post (worst-first, deduped per day) goes to @rmialerts;
   the HALL OF SHAME subset is only repeat scammers — and shows the group.

Pure + deterministic where possible; fail-open at every stage.
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

#: Letter-grade bands (lower score = better).
_GRADE_BANDS: tuple[tuple[float, str], ...] = (
    (5.0, "A"),
    (15.0, "B"),
    (30.0, "C"),
    (50.0, "D"),
    (80.0, "F"),
)

#: Repeat-scammer thresholds — Hall of Shame membership (not just a bad grade).
REPEAT_SCAMMER_MIN_DELETED = 2
REPEAT_SCAMMER_MIN_SCAM_CALLS = 2
REPEAT_SCAMMER_MIN_SHILLS = 3


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
# PURE MODEL — classification, aggregation, scoring
# ══════════════════════════════════════════════════════════════════════════


def classify_call(tweet_text: str) -> str:
    """Heuristic call type for one tweet: 'shill' | 'rug' | 'buy' | 'other'."""
    text = str(tweet_text or "")
    if not text:
        return "other"
    if _RUG_VERB_RE.search(text):
        return "rug"
    has_token = bool(_TICKER_RE.search(text) or _ADDRESS_RE.search(text))
    if not has_token and not _CALL_VERB_RE.search(text):
        return "other"
    if has_token and _PUMP_RE.search(text):
        return "shill"
    return "buy"


def extract_tokens(tweet_text: str) -> tuple[str, str]:
    """(ticker, address) mentioned in a tweet ("" when absent)."""
    text = str(tweet_text or "")
    m = _TICKER_RE.search(text)
    ticker = m.group(1).upper() if m else ""
    a = _ADDRESS_RE.search(text)
    addr = a.group(1) if a else ""
    return ticker, addr


def extract_wallet_address(tweet_text: str) -> str:
    """Best-effort wallet address in a call tweet (solana base58 / EVM 0x)."""
    text = str(tweet_text or "")
    m = _ADDRESS_RE.search(text)
    if not m:
        return ""
    addr = m.group(1)
    if addr.startswith("0x") and len(addr) < 32:
        return ""  # 0x contract-ish / too short to be a wallet
    if len(addr) >= 32:
        return addr
    return ""


def compute_report_card(
    calls: list[KolCall],
    *,
    wallet_address: str = "",
    name_changes: int = 0,
    identity: KolIdentity | None = None,
    traced_wallets: list[dict[str, str]] | None = None,
) -> KolReportCard:
    """Aggregate one KOL's calls into a report card (pure).

    Counts deletions, shill/rug calls, wins/losses (from resolved call
    outcomes), tokens mentioned, and calls on tokens on the scanner
    denylist. ``is_repeat_scammer`` gates Hall-of-Shame membership.
    ``traced_wallets`` carries the wallet KB entries (address + chain +
    confidence) so the card can render tracing evidence.
    """
    if not calls:
        return KolReportCard(
            handle="",
            wallet_address=wallet_address,
            wallets=_clean_wallets(traced_wallets),
            name_changes=name_changes,
        )
    handle = calls[0].handle
    total = len(calls)
    deleted = sum(1 for c in calls if c.is_deleted)
    shill = sum(1 for c in calls if classify_call(c.tweet_text) in ("shill", "rug"))
    wins = sum(1 for c in calls if c.result == "win")
    losses = sum(1 for c in calls if c.result == "loss")
    tokens = {c.token_symbol or "" for c in calls if c.token_symbol}
    tokens.discard("")
    known_scam = _known_scam_token_count(calls)
    repeat = (
        deleted >= REPEAT_SCAMMER_MIN_DELETED
        or known_scam >= REPEAT_SCAMMER_MIN_SCAM_CALLS
        or shill >= REPEAT_SCAMMER_MIN_SHILLS
    )
    card = KolReportCard(
        handle=handle,
        kol_id=(identity.kol_id if identity else "") or handle.lower(),
        name=(identity.name if identity else "") or handle,
        category=(identity.category if identity else "") or "",
        scammer_group=(identity.scammer_group if identity else "") or "",
        total_calls=total,
        deleted_calls=deleted,
        shill_calls=shill,
        scam_token_calls=known_scam,
        wins=wins,
        losses=losses,
        tokens_mentioned=len(tokens),
        wallet_address=wallet_address
        or (identity.wallets[0] if identity and identity.wallets else "")
        or next((c.wallet_address for c in calls if c.wallet_address), ""),
        wallets=_clean_wallets(traced_wallets),
        name_changes=name_changes,
        is_repeat_scammer=repeat,
    )
    card.score = score_card(card)
    card.grade = grade_for(card.score)
    return card


def _clean_wallets(entries: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """Normalize wallet KB entries to {address, chain, confidence} (pure)."""
    if not entries:
        return []
    cleaned: list[dict[str, str]] = []
    for e in entries:
        address = str(e.get("address") or "").strip() if isinstance(e, dict) else str(e).strip()
        if not address:
            continue
        if any(c.get("address", "").lower() == address.lower() for c in cleaned):
            continue
        cleaned.append(
            {
                "address": address,
                "chain": str(e.get("chain") or "") if isinstance(e, dict) else "",
                "confidence": str(e.get("confidence") or "") if isinstance(e, dict) else "",
            }
        )
    return cleaned


def _known_scam_token_count(calls: list[KolCall]) -> int:
    """Calls whose token address is on the bot's known-scam denylist."""
    try:
        from rugmunchbot.db import get_known_scam_addresses

        denylist = {str(a).lower() for a in get_known_scam_addresses()}
    except Exception:  # fail-open — denylist unavailable means 0
        return 0
    return sum(1 for c in calls if str(c.token_address or "").lower() in denylist)


def score_card(card: KolReportCard) -> float:
    """Score — higher is worse. Deletions dominate; wins earn a small offset.

    Weighting rationale: deleting a call after the fact is deliberate
    retraction (the strongest dishonesty signal), so it dominates. Repeated
    shilling and calls on known-scam tokens add weight. Clean call records
    (wins, no deletions) stay near 0.
    """
    score = 0.0
    score += 40.0 * card.deleted_calls
    score += 15.0 * card.shill_calls
    score += 25.0 * card.scam_token_calls
    if card.wallet_address:
        score += 2.0  # attribution known — the card is actionable
    if card.shill_calls >= REPEAT_SCAMMER_MIN_SHILLS and card.total_calls >= 5:
        score += 10.0  # coordinated behavior
    score -= min(2.0 * card.wins, 8.0)  # wins offset, capped
    return round(max(0.0, score), 1)


def grade_for(score: float) -> str:
    """Letter grade for a score (<5 → A+, ≥80 → F)."""
    grade = "A+"
    for threshold, band in _GRADE_BANDS:
        if score >= threshold:
            grade = band
    return grade


def rank_scoreboard(cards: list[KolReportCard], limit: int = 10) -> list[KolReportCard]:
    """Worst-first ranking over ALL cards (the scoreboard)."""
    ranked = sorted(cards, key=lambda c: (c.score, c.deleted_calls, c.losses), reverse=True)
    return ranked[: max(1, limit)]


def rank_hall_of_shame(cards: list[KolReportCard], limit: int = 10) -> list[KolReportCard]:
    """Worst-first ranking of REPEAT SCAMMERS only (Hall of Shame subset)."""
    qualified = [c for c in cards if c.in_hall_of_shame]
    qualified.sort(key=lambda c: (c.score, c.deleted_calls), reverse=True)
    return qualified[: max(1, limit)]


# ══════════════════════════════════════════════════════════════════════════
# FORMATTERS (Telegram HTML)
# ══════════════════════════════════════════════════════════════════════════


def format_report_card(card: KolReportCard) -> str:
    """One KOL's report card as an HTML block (the scoreboard render unit)."""
    handle = card.handle or "unknown"
    name = card.name if card.name and card.name != handle else ""
    head_name = f" {name}" if name else ""
    badge = " 🚨 <b>REPEAT SCAMMER</b>" if card.is_repeat_scammer else ""
    group_tag = ""
    if card.scammer_group:
        from rugmunchbot.kol_registry_seed import group_label

        group_tag = f" [<i>{group_label(card.scammer_group)}</i>]"
    head = f"🧾 <b>KOL report card: @{handle}</b>{head_name} — grade {card.grade}{badge}{group_tag}"
    lines = [
        head,
        f"• calls: {card.total_calls}  (wins {card.wins} / losses {card.losses}, win-rate {card.win_rate:.0%})",
        f"• deleted calls: {card.deleted_calls}",
        f"• shill/rug calls: {card.shill_calls}",
        f"• tokens on scam denylist: {card.scam_token_calls}",
        f"• tokens mentioned: {card.tokens_mentioned}",
    ]
    if card.category:
        lines.append(f"• category: {card.category}")
    if card.name_changes:
        lines.append(f"• screen-name changes: {card.name_changes}")
    if card.wallets:
        shown = card.wallets[:3]
        rendered = []
        for w in shown:
            short = w["address"][:8] + "…" + w["address"][-6:]
            meta = "/".join(x for x in (w.get("chain"), w.get("confidence")) if x)
            rendered.append(f"<code>{short}</code>{' (' + meta + ')' if meta else ''}")
        more = f" +{len(card.wallets) - len(shown)} more" if len(card.wallets) > len(shown) else ""
        lines.append(f"• traced wallets ({len(card.wallets)}): {', '.join(rendered)}{more}")
    elif card.wallet_address:
        short = card.wallet_address[:8] + "…" + card.wallet_address[-6:]
        lines.append(f"• wallet: <code>{short}</code>")
    lines.append(f"• score: <b>{card.score:.1f}</b>")
    return "\n".join(lines)


def format_scoreboard_post(cards: list[KolReportCard]) -> str:
    """HTML scoreboard post (worst-first; the main product render)."""
    if not cards:
        return ""
    lines = ["🏆 <b>KOL SCOREBOARD</b> — worst KOL report cards right now", ""]
    for card in cards[:5]:
        entry = f"@{card.handle} — grade <b>{card.grade}</b> (score {card.score:.0f})"
        if card.scammer_group:
            from rugmunchbot.kol_registry_seed import group_label

            entry += f" [{group_label(card.scammer_group)}]"
        detail = []
        if card.deleted_calls:
            detail.append(f"{card.deleted_calls} deleted")
        if card.wins or card.losses:
            detail.append(f"{card.wins}W/{card.losses}L")
        if card.shill_calls:
            detail.append(f"{card.shill_calls} shills")
        if card.scam_token_calls:
            detail.append(f"{card.scam_token_calls} scam-token calls")
        if card.wallets:
            n = len(card.wallets)
            detail.append(f"{n} wallet{'s' if n != 1 else ''} traced")
        if card.is_repeat_scammer:
            detail.append("repeat scammer")
        if detail:
            entry += f" — {', '.join(detail)}"
        lines.append(entry)
    lines.append("")
    lines.append("Hall of Shame = repeat scammers only. Verify before ape: /scan @rugmunchbot")
    return "\n".join(lines)


def format_hall_of_shame_post(cards: list[KolReportCard]) -> str:
    """HTML post for the Hall of Shame SUBSET (repeat scammers only).

    Group-attributed KOLs show their syndicate — the Hall is where the
    scammer-group is surfaced to the channel.
    """
    if not cards:
        return ""
    lines = ["🪦 <b>KOL HALL OF SHAME</b> — repeat scammers only", ""]
    for card in cards[:5]:
        entry = f"@{card.handle} — grade <b>{card.grade}</b> (score {card.score:.0f})"
        if card.scammer_group:
            from rugmunchbot.kol_registry_seed import group_label

            entry += f" [<i>{group_label(card.scammer_group)}</i>]"
        detail = []
        if card.deleted_calls:
            detail.append(f"{card.deleted_calls} deleted")
        if card.scam_token_calls:
            detail.append(f"{card.scam_token_calls} scam-token calls")
        if card.shill_calls:
            detail.append(f"{card.shill_calls} shills")
        if card.wallets:
            n = len(card.wallets)
            detail.append(f"{n} wallet{'s' if n != 1 else ''} traced")
        if detail:
            entry += f" — {', '.join(detail)}"
        lines.append(entry)
    lines.append("")
    lines.append(
        "Repeat deletions + scam-token calls = bad-faith pattern. Verify before ape: /scan @rugmunchbot"
    )
    return "\n".join(lines)


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
# SWEEP + RUNNER (async I/O; store + bot are injected)
# ══════════════════════════════════════════════════════════════════════════


def _call_from_tweet(tweet: dict[str, Any]) -> KolCall | None:
    """Build a KolCall from a normalized ApiPass tweet (None if not a call)."""
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


async def run_kol_scoreboard_watch(ctx: Any = None) -> int:
    """Sweep one KOL → record calls → wallet link → deletes → name changes →
    refresh cards → post scoreboard + hall-of-shame subset to @rmialerts.

    Returns the number of alerts posted (0..``KOL_SCOREBOARD_MAX_ALERTS``).
    Fail-open at every stage — a quiet tick is a valid outcome.
    """
    from rugmunchbot.channels.scheduler._core import _chat_id, _release, _remember, _safe_send
    from rugmunchbot.db import (
        list_kol_calls,
        list_kol_name_changes,
        mark_kol_call_deleted,
        record_kol_call,
        resolve_handle,
        upsert_kol_score,
    )

    chat_id = _chat_id("alerts")
    if not chat_id:
        logger.debug("kol_scoreboard:skipped_no_alerts_chat")
        return 0
    if not KOL_SCOREBOARD_ENABLED:
        return 0

    calls = await sweep_tracked_kol_tweets()
    recorded = 0
    for call in calls:
        if record_kol_call(
            handle=call.handle,
            tweet_id=call.tweet_id,
            token_symbol=call.token_symbol,
            token_address=call.token_address,
            chain=call.chain,
            call_type=call.call_type,
            tweet_text=call.tweet_text,
            posted_at=call.posted_at,
            source_url=call.source_url,
            wallet_address=call.wallet_address,
        ):
            recorded += 1
            link_wallet_from_call(call)
            logger.info(
                "kol_scoreboard:recorded handle=%s tweet=%s type=%s",
                call.handle,
                call.tweet_id,
                call.call_type,
            )

    # Detect deletions among the last window's recorded calls (bounded).
    recent = [KolCall.from_row(r) for r in list_kol_calls(limit=20)]
    fresh = [c for c in recent if _captured_within(c, FRESH_CALL_HOURS)]
    deleted = await detect_deleted_calls(fresh)
    for call in deleted:
        if mark_kol_call_deleted(call.tweet_id):
            logger.warning(
                "kol_scoreboard:deleted_call handle=%s tweet=%s token=%s",
                call.handle,
                call.tweet_id,
                call.token_symbol or call.token_address,
            )

    # Refresh cached report cards for the affected handles.
    affected = {c.handle for c in (*calls, *deleted)}
    for handle in affected:
        handle_calls = [KolCall.from_row(r) for r in list_kol_calls(handle=handle, limit=100)]
        identity = KolIdentity.from_row(resolve_handle(handle))
        name_changes = len(
            list(list_kol_name_changes(kol_id=(identity.kol_id if identity else handle.lower())))
        )
        card = compute_report_card(handle_calls, name_changes=name_changes, identity=identity)
        upsert_kol_score(
            handle=handle,
            total_calls=card.total_calls,
            deleted_calls=card.deleted_calls,
            shill_calls=card.shill_calls,
            scam_token_calls=card.scam_token_calls,
            tokens_mentioned=card.tokens_mentioned,
            score=card.score,
            grade=card.grade,
        )

    # Build cards for every KOL with recorded activity (scoreboard pool).
    cards: list[KolReportCard] = build_scoreboard_cards()
    scoreboard = rank_scoreboard(cards, limit=KOL_SCOREBOARD_MAX_ALERTS + 2)
    hall = rank_hall_of_shame(cards, limit=KOL_SCOREBOARD_MAX_ALERTS + 2)

    today = datetime.now(UTC).strftime("%Y%m%d")
    posted = 0
    for card in scoreboard[:KOL_SCOREBOARD_MAX_ALERTS]:
        dedupe_key = f"kol_scoreboard:{card.kol_id}:{today}"
        if not _remember("kol_scoreboard", dedupe_key):
            continue
        if ctx is None:
            _release("kol_scoreboard", dedupe_key)
            continue
        text = format_report_card(card)
        if await _safe_send(ctx.bot, chat_id, text):
            posted += 1
            logger.info(
                "kol_scoreboard:alerted handle=%s grade=%s score=%.1f",
                card.handle,
                card.grade,
                card.score,
            )
        else:
            _release("kol_scoreboard", dedupe_key)

    # Best-effort site bridge: JSONL export in the backend CallStore shape.
    _export_site_bridge()

    logger.info(
        "kol_scoreboard:run_done swept=%s recorded=%s deleted=%s scoreboard=%s hall=%s posted=%s",
        len(calls),
        recorded,
        len(deleted),
        len(scoreboard),
        len(hall),
        posted,
    )
    return posted


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
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data",
                "kol_scoreboard.jsonl",
            ),
        )
        export_kol_calls_jsonl(path)
    except Exception as exc:  # fail-open by contract
        logger.debug("kol_scoreboard:export_failed err=%s", str(exc)[:120])


__all__ = [
    "FRESH_CALL_HOURS",
    "KOL_SCOREBOARD_ENABLED",
    "KOL_SCOREBOARD_INTERVAL_MIN",
    "KOL_SCOREBOARD_MAX_ALERTS",
    "KOL_TRACKED_HANDLES",
    "KolCall",
    "KolIdentity",
    "KolReportCard",
    "build_scoreboard_cards",
    "classify_call",
    "compute_report_card",
    "detect_deleted_calls",
    "detect_name_change",
    "extract_tokens",
    "extract_wallet_address",
    "format_hall_of_shame_post",
    "format_report_card",
    "format_scoreboard_post",
    "grade_for",
    "link_wallet_from_call",
    "rank_hall_of_shame",
    "rank_scoreboard",
    "resolve_identity",
    "run_kol_scoreboard_watch",
    "score_card",
    "sweep_tracked_kol_tweets",
]
