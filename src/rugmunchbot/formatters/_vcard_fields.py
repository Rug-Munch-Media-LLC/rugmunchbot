"""Provenance primitives for the verdict-first scan card.

Every number on the card carries the data source that produced it and the
time it was observed — ``liq $1.2M · dexscreener · 12:04Z``. A value the
scan could not establish renders ``—`` plus the reason, never a fabricated
``0`` (keep-it-honest rule, same as the legacy Market Data block).

Source labels are grounded in the bot's own fill order:

  * ``dexscreener`` — price / mcap / liquidity / volume / price change
    (``tools/scanner/_deployer._supplement_deployer_socials`` and the v2
    DexScreener pair snapshot);
  * ``goplus``     — honeypot, buy/sell tax, contract verification
    (``tools/scanner/_providers`` GoPlus → honeypot.is chain);
  * ``onchain``    — holders, top-10 share, mint/freeze authority
    (``tools/scanner/_solana_onchain`` / RPC holder counts);
  * ``engine``     — launch cohorts, bundle verdict, deployer resolution
    (own scanner background phases).

A payload carrying its own ``data_sources`` mapping overrides the label for
that field (``{"liquidity": "dexscreener-v2"}``), so provenance stays
truthful when a fill path changes.

One public entry point: :func:`field_line`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

DASH = "\u2014"

#: Field key -> default source label (see module docstring).
SOURCES: dict[str, str] = {
    "price": "dexscreener",
    "mcap": "dexscreener",
    "liquidity": "dexscreener",
    "volume_24h": "dexscreener",
    "change_24h": "dexscreener",
    "holders": "onchain",
    "top10": "onchain",
    "mint_auth": "onchain",
    "freeze_auth": "onchain",
    "honeypot": "goplus",
    "taxes": "goplus",
    "verified": "goplus",
    "cohorts": "engine",
    "bundle": "engine",
    "deployer": "engine",
}

#: Why a field is empty, shown in place of the number.
REASONS: dict[str, str] = {
    "price": "no DexScreener pair",
    "mcap": "no pair market cap",
    "liquidity": "no pair liquidity",
    "volume_24h": "no 24h volume",
    "change_24h": "no 24h change",
    "holders": "no holder data",
    "top10": "no concentration data",
    "mint_auth": "authority not read",
    "freeze_auth": "authority not read",
    "honeypot": "no security answer",
    "taxes": "no tax data",
    "verified": "not read",
    "cohorts": "cohort engine not run",
    "bundle": "bundle engine not run",
    "deployer": "deployer not resolved",
}


def num(value: Any) -> float | None:
    """A real measurement as float, else ``None`` (bools/strings are not numbers).

    Used by the forensic blocks: a missing key, a ``None`` and an ``"n/a"``
    string all mean "not measured", so the caller renders nothing instead of
    a fabricated ``0%``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def short_addr(address: Any, head: int = 4, tail: int = 4) -> str:
    """``0xab…c123`` for on-card rows; short addresses pass through whole."""
    text = str(address or "")
    if len(text) <= head + tail + 2:
        return text
    return f"{text[:head]}\u2026{text[-tail:]}"


def stamp(value: Any = None) -> str:
    """Compact ``HH:MMZ`` observation time (UTC, now when unparsable)."""
    moment: datetime | None = None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, (int, float)) and value > 0:
        seconds = value / 1000 if value > 1e11 else float(value)
        moment = datetime.fromtimestamp(seconds, tz=UTC)
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            moment = None
    if moment is None:
        moment = datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%H:%MZ")


def source_for(r: dict[str, Any], key: str) -> str:
    """Source label for ``key`` — payload override first, then the default."""
    override = r.get("data_sources")
    if isinstance(override, dict):
        label = override.get(key)
        if isinstance(label, str) and label.strip():
            return label.strip()[:20]
    return SOURCES.get(key, "scan")


def field_line(label: str, key: str, value: str | None, source: str, ts: str) -> str:
    """One provenance row: ``Label   value · source · HH:MMZ``.

    ``value`` is the caller's formatted text. ``None`` / ``""`` / ``—`` means
    the scan established nothing: the row then names the source that did not
    answer and why, so a blank is never read as a zero.
    """
    text = "" if value is None else str(value).strip()
    if text in ("", DASH):
        return f"  {label:<12} {DASH} \u00b7 {source}: {REASONS.get(key, 'not reported')}"
    return f"  {label:<12} <b>{text}</b> \u00b7 {source} \u00b7 {ts}"
