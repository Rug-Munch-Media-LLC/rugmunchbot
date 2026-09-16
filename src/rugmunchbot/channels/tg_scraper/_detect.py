"""Scam/shill detection over scraped Telegram messages.
import time

Three signals, all fail-open and pure (unit-testable without network):
  1. Token mentions — $TICKER, EVM 0x addresses, Solana base58 addresses
     (reuses the canonical ``shared_utils`` regexes).
  2. Scam language patterns — pump-and-dump, guaranteed returns, doubling
     scams, seed-phrase harvesting, fake support, airdrop bait.
  3. Known-scammer cross-ref — sender handle probed against the backend
     shill API (wall_of_shame / 945-wallet KB attribution), fail-open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rugmunchbot._vendor.shared_utils import is_evm_address, is_solana_address
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.tg_scraper")

_TICKER_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9_]{1,20})\b")
_EVM_RE = re.compile(r"0x[a-fA-F0-9]{40}")
# Solana base58: 32-44 chars, no 0/O/I/l
_SOL_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


@dataclass(slots=True)
class ScamHit:
    """One scam-pattern match inside a message."""

    pattern_id: str
    severity: str  # critical | high | medium
    matched_text: str


@dataclass(slots=True)
class Detection:
    """Full detection result for one scraped message."""

    tickers: list[str] = field(default_factory=list)
    evm_addresses: list[str] = field(default_factory=list)
    solana_addresses: list[str] = field(default_factory=list)
    hits: list[ScamHit] = field(default_factory=list)
    known_scammer: bool = False

    @property
    def is_suspicious(self) -> bool:
        return bool(self.hits) or self.known_scammer


#: (pattern_id, compiled regex, severity) — ordered by severity for docs.
SCAM_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "doubling_scam",
        re.compile(
            r"send\s+(?:me\s+)?[\d.]+\s*(?:eth|bnb|sol|usdt|usdc)\b.{0,60}\b(?:get|receive|back)\s+\d",
            re.I,
        ),
        "critical",
    ),
    (
        "guaranteed_returns",
        re.compile(r"\b(?:guaranteed|risk[- ]free)\s+(?:returns?|profits?|gains?|x\d+)\b", re.I),
        "high",
    ),
    (
        "pump_signal",
        re.compile(
            r"\b(?:pump\s+(?:and\s+dump|group|signal)|pnd|buy\s+now\s+before\s+(?:listing|pump))\b",
            re.I,
        ),
        "high",
    ),
    (
        "seed_phrase_harvest",
        re.compile(
            r"\b(?:seed\s+phrase|private\s+key|recovery\s+phrase|12\s+words?)\b.{0,40}\b(?:enter|send|share|verify|sync|import)\b|\b(?:enter|share|verify|sync)\b.{0,40}\b(?:seed\s+phrase|private\s+key|recovery\s+phrase)\b",
            re.I,
        ),
        "critical",
    ),
    (
        "fake_support",
        re.compile(
            r"\b(?:official\s+)?(?:support|admin|team)\b.{0,30}\b(?:dm|message)\b|\bdm\b.{0,20}\b(?:support|admin|help)\b",
            re.I,
        ),
        "high",
    ),
    (
        "airdrop_bait",
        re.compile(
            r"\b(?:claim|connect\s+(?:your\s+)?wallet\s+(?:to\s+claim|for))\b.{0,40}\b(?:airdrop|reward|bonus|free)\b|\bfree\s+(?:tokens?|coins?|airdrop)\b",
            re.I,
        ),
        "medium",
    ),
    (
        "urgent_fomo",
        re.compile(
            r"\b(?:last\s+chance|only\s+\d+\s+(?:spots?|slots?|hours?)|closing\s+soon|before\s+it'?s\s+too\s+late)\b",
            re.I,
        ),
        "medium",
    ),
    (
        "impersonation",
        re.compile(
            r"\b(?:binance|coinbase|metamask|trust\s?wallet|elon\s?musk)\s+(?:giveaway|airdrop|support)\b",
            re.I,
        ),
        "high",
    ),
)


def extract_mentions(text: str) -> tuple[list[str], list[str], list[str]]:
    """Extract (tickers, evm_addresses, solana_addresses) from message text.

    Pure function. Tickers are uppercase ``$``-stripped; addresses are
    deduped and validated by the canonical shared_utils regexes.
    """
    if not text:
        return [], [], []
    tickers: list[str] = []
    seen_t: set[str] = set()
    for match in _TICKER_RE.finditer(text):
        ticker = match.group(1).upper()
        if ticker not in seen_t:
            seen_t.add(ticker)
            tickers.append(ticker)
    evm: list[str] = []
    sol: list[str] = []
    seen_a: set[str] = set()
    for match in _EVM_RE.finditer(text):
        addr = match.group(0)
        if addr not in seen_a and is_evm_address(addr):
            seen_a.add(addr)
            evm.append(addr)
    for match in _SOL_RE.finditer(text):
        addr = match.group(0)
        if addr in seen_a or not is_solana_address(addr):
            continue
        # Skip strings that already matched as EVM (impossible) or look like
        # ordinary words — is_solana_address enforces base58 shape.
        seen_a.add(addr)
        sol.append(addr)
    return tickers, evm, sol


def detect_scam_patterns(text: str) -> list[ScamHit]:
    """Match the scam-language pattern list against message text. Pure."""
    if not text:
        return []
    hits: list[ScamHit] = []
    for pattern_id, regex, severity in SCAM_PATTERNS:
        match = regex.search(text)
        if match:
            hits.append(
                ScamHit(pattern_id=pattern_id, severity=severity, matched_text=match.group(0)[:120])
            )
    return hits


# 2026-09-17: per-message probes at message rate (uncached) hammered the
# backend shill API (~0.6+/s sustained, 3s each). Cache results per handle —
# scammer status changes rarely; negative results cached too (fail-open).
_PROBE_CACHE: dict[str, tuple[float, bool]] = {}
_PROBE_CACHE_TTL_S = 3600.0
_PROBE_CACHE_MAX = 10_000


def _probe_cache_get(key: str) -> bool | None:
    hit = _PROBE_CACHE.get(key)
    if not hit:
        return None
    ts, value = hit
    if (time.time() - ts) >= _PROBE_CACHE_TTL_S:
        _PROBE_CACHE.pop(key, None)
        return None
    return value


def _probe_cache_put(key: str, value: bool) -> None:
    if len(_PROBE_CACHE) >= _PROBE_CACHE_MAX:
        _PROBE_CACHE.clear()
    _PROBE_CACHE[key] = (time.time(), value)


async def is_known_scammer(handle: str | None) -> bool:
    """Probe a sender handle against the backend shill API (wall_of_shame).

    Fail-open: any outage → False (unknown, not cleared — absence of signal
    is not innocence; the language patterns still apply). Results cached
    per handle (TTL) so message-rate traffic does not hammer the API.
    """
    if not handle:
        return False
    key = handle.lstrip("@").lower()
    cached = _probe_cache_get(key)
    if cached is not None:
        return cached
    try:
        from rugmunchbot.services import shill_api

        result = await shill_api.probe_handle(key)
        if not result:
            return False
        known = bool(result.get("scammer") or result.get("flagged") or result.get("known"))
        _probe_cache_put(key, known)
        return known
    except Exception as exc:  # fail-open by contract
        logger.debug("tg_scraper:scammer_probe_failed handle=%s err=%s", key, str(exc)[:120])
        return False


async def detect_message(message: Any) -> Detection:
    """Run full detection over one TgMessage. Returns a Detection dataclass.

    Kept async only for the known-scammer probe; everything else is pure.
    """
    tickers, evm, sol = extract_mentions(message.text or "")
    hits = detect_scam_patterns(message.text or "")
    known = await is_known_scammer(getattr(message, "sender_handle", None))
    return Detection(
        tickers=tickers,
        evm_addresses=evm,
        solana_addresses=sol,
        hits=hits,
        known_scammer=known,
    )


def detection_summary(detection: Any) -> str:
    """One-line human summary for logs/evidence. Never raises."""
    try:
        parts: list[str] = []
        if detection.tickers:
            parts.append("tickers=" + ",".join(detection.tickers[:5]))
        if detection.evm_addresses or detection.solana_addresses:
            parts.append(
                f"addresses={len(detection.evm_addresses) + len(detection.solana_addresses)}"
            )
        if detection.hits:
            parts.append("patterns=" + ",".join(h.pattern_id for h in detection.hits))
        if detection.known_scammer:
            parts.append("known_scammer=1")
        return " ".join(parts)
    except Exception:
        return ""
