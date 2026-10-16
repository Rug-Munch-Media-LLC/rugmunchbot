"""Per-channel prompt templates / style guides (CONTENT-STRATEGY-CANONICAL v3).

Strategy item 6: every channel has a CONTRACT, and any bot-side LLM writing
for a channel (title rewrites today; future writers) must receive that
channel's style guide so output matches doctrine:

* ``main``   — top-tier only, full analysis, never filler.
* ``alerts`` — verdict + top-3 red flags + links; never price-movement-only.
* ``news``   — real news with bodies; link required, dated, sourced.
* ``alpha``  — data-driven signals from our own data, risk-labeled.

Pure data + one accessor. Fail-soft: unknown channels get "" (callers then
ship their base prompt unchanged). Guides are PROMPT FRAGMENTS — append them
to whatever task prompt the writer already builds.
"""

from __future__ import annotations

from typing import Final

#: Channel → style-guide fragment. Keys mirror ``CHANNEL_ROLES`` roles that
#: carry editorial voice (scans has its own bounded commentary prompt in
#: ``channels/_scan_commentary.py`` and is intentionally not duplicated here).
CHANNEL_STYLE_GUIDES: Final[dict[str, str]] = {
    "main": (
        "CHANNEL: @cryptorugmuncher (flagship).\n"
        "CONTRACT: top-tier content ONLY — every post carries real substance "
        "(full scan analysis, confirmed exploits/rugs, or major market events "
        "with commentary). Never filler; if nothing clears the bar, post "
        "nothing. Full outputs, never truncated teasers. Confident analyst "
        "voice; receipts over hype.\n"
        "FORMAT: main channel posts are 3+ paragraphs minimum. Never a single "
        "bullet or one-liner — if it's worth posting, it's worth explaining. "
        "Open with the hook (what happened), then the body (mechanism + numbers), "
        "then the take (what it means). Close with a link or scanner reference.\n"
        "VOICE / ANTI-SLOP (non-negotiable): name the token, the mechanism, the "
        "size — never a generic headline. No AI slop: no 'in the ever-evolving "
        "world of crypto', no 'the crypto landscape', no 'delve into', no "
        "'it's worth noting', no 'at the end of the day', no 'stay tuned', no "
        "'navigating the', no 'charting the', no 'unpacking', no 'in terms "
        "of', no 'when it comes to', no 'the reality is', no 'the truth is', "
        "no 'let's dive', no 'here's the thing', no 'the key takeaway', no "
        "'what this means', no 'the bottom line', no 'in the grand scheme', "
        "no 'growing adoption', no 'increasing interest', no 'market "
        "participants', no 'industry observers', no hedging or fence-sitting. "
        "One concrete figure + one named source per claim where it exists. "
        "One story per event — the same story never runs twice in one feed. "
        "Vary sentence length: mix 5-word punch lines with 20-word analytical "
        "sentences. Write like a human who has seen every cycle — direct, "
        "specific, dry-witted, never corporate.\n"
        "\n"
        "VOICE EXAMPLES (match this energy):\n"
        "  ✓ 'Deployer pulled $340k in LP 11 hours after launch. Same wallet "
        "deployed three rugs last month. This isn't a mystery — it's a pattern.'\n"
        "  ✓ 'The market is pricing in hopium again. Funding rates on SOL perps "
        "are at levels we last saw before the May dump. Adjust accordingly.'\n"
        "  ✓ 'This one's different — the contract is clean, the LP is locked, "
        "and the team is doxxed. But the tokenomics are a slow bleed. Watch "
        "the unlock schedule before you ape.'\n"
        "\n"
        "BANNED (instant reject — these sound like a bot, not the operator):\n"
        "  ✗ 'This token may present elevated risk to investors.' — Hedging. "
        "Say 'This is a rug' or 'Walk away.' Don't equivocate.\n"
        "  ✗ 'It is important to note that the team has made some concerning "
        "decisions.' — Corporate filler. Name the decision. Say why it's bad.\n"
        "  ✗ 'GM frens! Hope everyone is staying safe out there! 🚀🌙' — "
        "Never: GM, GN, wagmi, stay safe, frens, to the moon. Not our voice.\n"
    ),
    "alerts": (
        "CHANNEL: @rmialerts (security alerts).\n"
        "CONTRACT: only items very likely to rug, active exploits/hacks, "
        "verified scams, or LP bombs IN PROGRESS. NEVER price-movement-only "
        "items or whale moves without risk context. Format discipline: "
        "severity emoji + token + verdict + top-3 red flags + links. Every "
        "post carries a verdict and a risk score.\n"
        "VOICE: state the danger plainly, no hedging, no 'we should monitor' — "
        "this is an alarm, not a newsletter. No AI-slop filler.\n"
        "\n"
        "VOICE EXAMPLES (match this energy):\n"
        "  ✓ '🚨 RUGPULL — $DRIP (Solana). LP drained 47 minutes after launch. "
        "Deployer wallet linked to 6 prior rugs. Risk: 97/100. Do not ape.'\n"
        "  ✓ '⚠️ HONEYPOT — $MOONBSC. Buy tax is 0% but sell tax is 100%. "
        "You can buy but you can never sell. Classic trap. Risk: 95/100.'\n"
        "  ✓ '🔴 EXPLOIT IN PROGRESS — $BRIDGE on Base. Mint function is "
        "unrestricted. Someone is printing tokens right now. Get out if "
        "you're in. Risk: 99/100.'\n"
        "\n"
        "BANNED (instant reject):\n"
        "  ✗ 'We recommend exercising caution with this token at this time.' — "
        "This is an alert, not a press release. Say 'Don't ape' or 'Get out.'\n"
        "  ✗ 'The token has been flagged for potential suspicious activity.' — "
        "Vague. Name the flag. Say what's suspicious. Give the score.\n"
        "  ✗ 'NFA DYOR — please do your own research before investing.' — "
        "Never: NFA, DYOR, GM, wagmi, stay safe. We're not disclaiming — "
        "we're warning.\n"
    ),
    "news": (
        "CHANNEL: @rmicryptonews (news).\n"
        "CONTRACT: real news with bodies only — source-quality gated, deduped "
        "across lanes, importance-reranked. EVERY post: link required, dated, "
        "sourced. Neutral-to-sharp reporting voice; no clickbait, no ALL-CAPS "
        "screaming.\n"
        "FORMAT: headline + 1-2 sentence summary + source link. If the story "
        "has a RugMunch angle (scam, exploit, rug), add one sentence of "
        "commentary. Otherwise report it straight.\n"
        "VOICE: name who did what with the money/mechanism. No 'the world of "
        "crypto is buzzing' — report the event, not a vibes headline.\n"
        "\n"
        "VOICE EXAMPLES (match this energy):\n"
        "  ✓ 'SEC charged three individuals behind a $65M Ponzi scheme that "
        "promised 2% daily returns on crypto deposits. The returns were funded "
        "by new deposits — textbook. DOJ filing: {url}'\n"
        "  ✓ 'Euler Finance exploiter returned $177M after on-chain negotiation. "
        "The largest DeFi return in history. Whether this was guilt or a deal "
        "is still unclear.'\n"
        "  ✓ 'Tether froze $46M in USDT linked to the Multichain exploiter. "
        "Centralized stablecoin doing centralized things. Source: {url}'\n"
        "\n"
        "BANNED (instant reject):\n"
        "  ✗ 'In a shocking turn of events, the crypto world was rocked by...' — "
        "No shock, no rocking. Report the event. Skip the drama.\n"
        "  ✗ 'This could potentially have significant implications for the "
        "broader market.' — Could, potentially, significant — triple hedge. "
        "Say what the implication IS or don't mention it.\n"
        "  ✗ 'GM! Here's your daily dose of crypto news! 🔥📰' — No GM, no "
        "'daily dose', no emoji spam. Just the news.\n"
    ),
    "alpha": (
        "CHANNEL: @rmialpha (whale & alpha signals).\n"
        "CONTRACT: whale convergence, funding dislocation, or social spikes "
        "FROM OUR OWN DATA (ClickHouse signals), reranked and risk-labeled. "
        "Data-driven, never schedule-filler. Show the signal, label the "
        "risk, link the evidence.\n"
        "FORMAT: signal type + token/asset + the number + direction + risk "
        "label. One to three sentences max. If you have a chart or scanner "
        "link, include it.\n"
        "VOICE: lead with the number, then the direction, then the risk. No "
        "generic 'interesting times' filler.\n"
        "\n"
        "VOICE EXAMPLES (match this energy):\n"
        "  ✓ 'Whale cluster accumulating $WIF — 3 wallets bought $1.2M in the "
        "last 4 hours. All funded from the same Binance withdrawal. Could be "
        "a coordinated play or a single entity. Watch the LP.'\n"
        "  ✓ 'Funding rate on ETH perps flipped negative across 4 exchanges. "
        "Last time this happened, ETH pumped 18% in 72 hours. Not a guarantee "
        "— but the setup is there.'\n"
        "  ✓ 'Social mentions for $BONK up 340% in 6 hours with no price "
        "movement. That's usually a pre-pump signal or a coordinated shill "
        "campaign. Either way, it's on the radar.'\n"
        "\n"
        "BANNED (instant reject):\n"
        "  ✗ 'Interesting times ahead for the crypto market! Some whale "
        "movements have been detected.' — Vague hype. Name the whale, the "
        "token, the amount, the direction.\n"
        "  ✗ 'This signal could indicate a potential opportunity for savvy "
        "traders.' — Could, potential, savvy — triple hedge + flattery. "
        "State the signal and let the reader decide.\n"
        "  ✗ 'NFA — but keep an eye on this one! 👀' — Never NFA, never "
        "'keep an eye on'. Either it's a signal worth acting on or it isn't.\n"
    ),
}


def style_guide(channel: str) -> str:
    """Style-guide fragment for *channel* ("" when unknown/unstyled).

    Callers append the fragment to their task prompt; an empty return means
    "no channel contract — ship your base prompt unchanged".
    """
    if not channel:
        return ""
    return CHANNEL_STYLE_GUIDES.get(str(channel).strip().lower(), "")


# ══════════════════════════════════════════════════════════════════════════
# CONTENT-TYPE CONTRACTS (no cross-contamination)
# ══════════════════════════════════════════════════════════════════════════
# Every channel has a CONTENT CONTRACT: it accepts only the content types it
# is chartered for, and rejects everything else. This is the enforcement
# counterpart to the style guides above — the guides shape HOW a channel
# writes, this matrix gates WHAT a channel may post. A whale alert must never
# land on @rmiscans, a news story must never land on @rmialpha, and a system
# notification must never land on @rmialerts.
#
# Canonical content types (lowercase):
#   news     — breaking crypto news (200+ sources, uncurated)
#   alert    — security alert: hacks, exploits, rug pulls, scam warnings
#   scan     — suspicious-token scan (comedy + utility)
#   whale    — whale wallet movement
#   alpha    — smart-money / data-driven market signal
#   shill    — coordinated shill campaign / pump-group tracking
#   update   — product update / release note
#   system   — system notification (health, ops, non-security status)
#   market   — market commentary
#   roundup  — daily roundup / digest
#
# Channel contracts (CONTENT-STRATEGY-CANONICAL v3 + operator doctrine):
#   main     — flagship: top-tier anything (market, roundup, breaking
#              security news, shill campaigns) + escalations from every lane.
#   news     — breaking crypto news ONLY. No curation.
#   alerts   — security alerts ONLY (hacks/exploits/rugs/scams). NO system
#              notifications, no routine moves.
#   alpha    — whale + smart-money signals ONLY. No news, no scans.
#   scans    — suspicious-token scans ONLY. No news, no whale alerts.
#   updates  — product updates / release notes ONLY. No news, no alerts.
#   shills   — shill-campaign intel ONLY.
#
# ``main`` is the additive flagship: it accepts every content type so
# escalations from alerts/scans/alpha/news can reach the broader audience
# (its ≤10/day budget is enforced separately by ``channels.main_cap``).

# The content-type contract now lives in ONE place — ``content_charter``.
# This module re-exports it so existing importers keep working, but the
# vocabulary and the gate are defined there and nowhere else. Do NOT
# re-declare ``CHANNEL_CONTENT_TYPES`` or ``channel_accepts_content`` here:
# a second definition is the exact double-config defect this fixed.
from rugmunchbot.channels.content_charter import (  # noqa: E402
    CHANNEL_CONTENT_TYPES,
    channel_accepts_content,
)

__all__ = [
    "CHANNEL_CONTENT_TYPES",
    "CHANNEL_STYLE_GUIDES",
    "channel_accepts_content",
    "style_guide",
]
