"""Pure signal extraction for one promoted message.

Every signal carries a ``family``. Two signals from the SAME family are one
observation, not corroboration — the gate relies on that (see ``gate.py``).
The extractor is pure: all external facts (token age, on-chain risk, channel
cadence, known-bad actors) arrive in the ``evidence`` mapping, so tests never
touch the network and the caller owns every I/O decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rugmunchbot.channels.shill_alerts.entities import find_entity

#: Signal families. Independence is counted per family, never per signal.
PROMOTION = "promotion"
DISCLOSURE = "disclosure"
ONCHAIN = "onchain"
IDENTITY = "identity"
CADENCE = "cadence"

#: Pump vocabulary. Matched case-insensitively as whole phrases.
HYPE_TERMS: tuple[str, ...] = (
    "guaranteed",
    "risk free",
    "risk-free",
    "no risk",
    "100x",
    "1000x",
    "10000x",
    "airdrop",
    "presale",
    "pre-sale",
    "whitelist",
    "listing soon",
    "next moonshot",
    "don't miss",
    "dont miss",
    "send it",
    "double your",
    "buy now",
    "last chance",
    "insider call",
)

#: Explicit paid-promotion disclosure markers.
DISCLOSURE_MARKERS: tuple[str, ...] = (
    "#ad",
    "#sponsored",
    "#paid",
    "#partnership",
    "paid promo",
    "paid partnership",
    "sponsored post",
    "advertisement",
    "promo paid",
)

#: Inbound-pressure markers that imply paid promotion when undisclosed.
PROMO_PRESSURE: tuple[str, ...] = (
    "dm me",
    "join my",
    "join the group",
    "ref link",
    "referral link",
    "use my code",
    "my link",
    "sign up with",
    "t.me/+",
)


@dataclass(frozen=True, slots=True)
class Signal:
    """One scored observation: what was seen, which family, how strong."""

    name: str
    family: str
    weight: float
    detail: str


@dataclass(frozen=True, slots=True)
class MessageSignals:
    """Signals for one message, plus the entity it is really about."""

    channel: str
    message_id: str
    entity: str
    signals: tuple[Signal, ...]

    @property
    def families(self) -> frozenset[str]:
        return frozenset(s.family for s in self.signals)

    @property
    def weight(self) -> float:
        return round(sum(s.weight for s in self.signals), 3)


def _has(text: str, terms: tuple[str, ...]) -> list[str]:
    return [t for t in terms if t in text]


def extract_signals(
    message: dict[str, Any],
    *,
    evidence: dict[str, Any] | None = None,
    repeat_channels: int = 0,
) -> MessageSignals:
    """Score one message. ``repeat_channels`` = distinct channels for its entity."""
    evidence = evidence or {}
    raw_text = str(message.get("text") or message.get("title") or "")
    text = raw_text.lower()
    channel = str(message.get("channel") or message.get("source_channel") or "unknown")
    message_id = str(message.get("message_id") or message.get("id") or "")
    # Entity comes from the ORIGINAL text: lowercasing would corrupt base58
    # Solana mints and the checksummed EVM case.
    entity = str(message.get("entity") or "") or find_entity(raw_text)
    found: list[Signal] = []

    if entity:
        found.append(Signal("contract_address", PROMOTION, 0.25, f"address {entity[:12]}…"))

    hype = _has(text, HYPE_TERMS)
    if hype:
        found.append(Signal("hype_lexicon", PROMOTION, 0.30, "terms: " + ", ".join(hype[:4])))

    if repeat_channels >= 3:
        found.append(
            Signal(
                "cross_channel_repeat",
                PROMOTION,
                0.45,
                f"identical promo in {repeat_channels} channels",
            )
        )

    # Paid promotion: pressure markers with no disclosure present.
    pressure = _has(text, PROMO_PRESSURE) or bool(message.get("referral_link"))
    if pressure and not _has(text, DISCLOSURE_MARKERS):
        found.append(
            Signal(
                "undisclosed_paid_promo",
                DISCLOSURE,
                0.35,
                "promo pressure without #ad/#sponsored disclosure",
            )
        )

    age = evidence.get("token_age_hours")
    if isinstance(age, (int, float)) and 0 <= float(age) < 72:
        found.append(Signal("fresh_token", ONCHAIN, 0.30, f"token age {float(age):.0f}h < 72h"))

    risk = evidence.get("onchain_risk") or {}
    if isinstance(risk, dict):
        level = str(risk.get("risk_level") or "").upper()
        flags = []
        if level in ("HIGH", "CRITICAL"):
            flags.append(f"{level} risk")
        if risk.get("honeypot"):
            flags.append("honeypot")
        if risk.get("bundle_detected"):
            flags.append("bundled launch")
        if int(risk.get("deployer_rugs") or 0) > 0:
            flags.append(f"deployer {int(risk['deployer_rugs'])} prior rug(s)")
        if flags:
            found.append(Signal("onchain_risk", ONCHAIN, 0.50, "; ".join(flags)))

    bad = [str(a) for a in (evidence.get("known_bad_actors") or []) if a]
    if channel.lstrip("@").lower() in {b.lstrip("@").lower() for b in bad}:
        found.append(Signal("known_bad_actor", IDENTITY, 0.45, f"{channel} on watchlist"))

    rate = evidence.get("channel_rate_per_hour")
    base = evidence.get("channel_baseline_per_hour")
    if (
        isinstance(rate, (int, float))
        and isinstance(base, (int, float))
        and base > 0
        and float(rate) >= 3 * float(base)
    ):
        found.append(
            Signal(
                "cadence_spike",
                CADENCE,
                0.30,
                f"{float(rate):.0f}/h vs baseline {float(base):.0f}/h",
            )
        )

    return MessageSignals(
        channel=channel,
        message_id=message_id,
        entity=entity,
        signals=tuple(found),
    )
