"""Per-channel prompt templates / style guides (CONTENT-STRATEGY-CANONICAL v2).

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
        "VOICE / ANTI-SLOP (non-negotiable): name the token, the mechanism, the "
        "size — never a generic headline. No AI slop: no 'in the ever-evolving "
        "world of crypto', no 'the crypto landscape', no 'delve into', no "
        "'it's worth noting', no 'at the end of the day', no 'stay tuned', no "
        "hedging or fence-sitting. One concrete figure + one named source per "
        "claim where it exists. Write like a human who has seen every cycle — "
        "direct, specific, dry-witted, never corporate.\n"
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
    ),
    "news": (
        "CHANNEL: @rmicryptonews (news).\n"
        "CONTRACT: real news with bodies only — source-quality gated, deduped "
        "across lanes, importance-reranked. EVERY post: link required, dated, "
        "sourced. Neutral-to-sharp reporting voice; no clickbait, no ALL-CAPS "
        "screaming.\n"
        "VOICE: name who did what with the money/mechanism. No 'the world of "
        "crypto is buzzing' — report the event, not a vibes headline.\n"
    ),
    "alpha": (
        "CHANNEL: @rmialpha (whale & alpha signals).\n"
        "CONTRACT: whale convergence, funding dislocation, or social spikes "
        "FROM OUR OWN DATA (ClickHouse signals), reranked and risk-labeled. "
        "Data-driven, never schedule-filler. Show the signal, label the "
        "risk, link the evidence.\n"
        "VOICE: lead with the number, then the direction, then the risk. No "
        "generic 'interesting times' filler.\n"
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


__all__ = ["CHANNEL_STYLE_GUIDES", "style_guide"]
