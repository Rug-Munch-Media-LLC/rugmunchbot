"""Pure helpers for the free-text AI chat pipeline (chat_ai.py) — re-export shim.

Extracted from ``handlers/chat_ai.py`` during the Batch 11 de-godding pass,
then split again (Batch 12): the formatting / prompt-building / response-parsing
helpers now live in ``_markup.py`` (markdown/HTML formatters), ``_quality.py``
(answer quality), ``_classify.py`` (question classification) and ``_local_ai.py``
(local canned replies). This module keeps the anti-abuse / handler message
constants (pure data) and re-exports the helpers so ``chat_ai/__init__.py`` and
the unit tests keep importing from here unchanged. Behavior is byte-for-byte
identical.
"""

from __future__ import annotations

import re

from app.domains.telegram.rugmunchbot.formatters import footer_links
from app.domains.telegram.rugmunchbot.handlers.chat_ai._classify import (
    MAX_ADDRESS_HINTS,
    _extract_addresses,
    _is_capability_question,
    _is_deep_question,
    _is_greeting_or_thanks,
    _is_meaningful_question,
    _is_self_improvement_question,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._local_ai import (
    _INTENT_RULES,
    _SELF_DESCRIPTION_ANSWER,
    _SELF_IMPROVEMENT_ANSWER,
    local_ai_response,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._markup import (
    CHUNK_MAX_CHARS,
    MAX_ANSWER_CHARS,
    _beautify_answer,
    _cap_answer,
    _format_scan_summary,
    _is_section_header,
    _markdown_bold_to_html,
    _markdown_to_html,
    _normalize_llm_markup,
    _raw_html_emphasis_to_markdown,
    _tables_to_lists,
    _unescape_llm_entities,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._quality import (
    _grounding_sources_count,
    is_degenerate_answer,
    strip_think_blocks,
)

# ══════════════════════════════════════════════
# ANTI-ABUSE REPLIES (ported from handlers/message.py)
# ══════════════════════════════════════════════

_INJECTION_MESSAGES: dict[str, str] = {
    "instruction-override": (
        "I'm designed to follow my original security instructions. I can't be re-prompted to ignore them."
    ),
    "secret-extraction": ("I don't reveal system prompts, API keys or internal configuration."),
    "role-switch": ("I'm Rug Munch's assistant — I don't switch to developer/uncensored modes."),
    "echo-poisoning": ("I don't echo raw prompts or messages — ask me a normal question instead."),
    "wallet-secret-harvest": ("Never share your seed phrase or private keys with anyone — including bots."),
    "wallet-drain-social": (
        "Be careful! Legitimate airdrops never ask you to connect your wallet or approve transactions to claim."
    ),
    "forced-promotion": ("I don't promote tokens or post messages on request."),
}

_KNOWN_SCAM_WARNING = (
    "\U0001f6a8 <b>Warning: Known Scam Address</b>\n\n"
    "The address you shared is on our <b>known scam denylist</b> — it has "
    "been reported by other users and flagged for suspicious activity.\n\n"
    "\U0001f4a1 <b>Do not interact with this token.</b>\n"
    "\u2022 Don't approve any transactions\n"
    "\u2022 Don't connect your wallet to its site\n"
    "\u2022 If you've bought it, watch for exit liquidity pulls\n\n"
    "Thanks for checking with Rug Munch first!"
)

# ══════════════════════════════════════════════
# TIER-3 FRIENDLY REDIRECT (ported from handlers/message.py)
# ══════════════════════════════════════════════

_REDIRECT_MESSAGE = (
    "\U0001f916 <b>Rug Munch AI</b>\n\n"
    "I can help with token scans, wallet analysis, market data, DeFi, "
    "crypto news and even coding questions. Try:\n\n"
    "\U0001f4ac <code>/chat &lt;question&gt;</code> \u2014 talk to me about anything\n"
    "\U0001f50d <code>/scan &lt;address&gt;</code> \u2014 security audit\n"
    "\U0001f4cc <code>$TOKEN</code> \u2014 instant price lookup\n"
    "\U0001f9db <code>/wallet &lt;addr&gt;</code> \u2014 wallet analysis\n"
    "\U0001f525 <code>/trending</code> \u2014 hot tokens by chain\n"
    "\U0001f6e1\ufe0f <code>/mev</code> \u2014 MEV protection\n"
    "\U0001f97e <code>/impersonation</code> \u2014 detect fake tokens\n"
    "\u23f0 <code>/deathclock</code> \u2014 token survival prediction\n"
    "\U0001f4da <code>/scamschool</code> \u2014 learn scam patterns"
    f"{footer_links()}"
)


# ══════════════════════════════════════════════
# HANDLER MESSAGES
# ══════════════════════════════════════════════

_FALLBACK_MESSAGE = (
    "\U0001f9e0 <b>AI brain temporarily unavailable</b>\n\n"
    "I couldn't reach the intelligence engine right now. Use "
    "<code>/scan &lt;address&gt;</code> for direct analysis, or try again "
    "in a moment."
)

_DEGENERATE_MESSAGE = (
    "I couldn't put together a proper answer for that. "
    "Try rephrasing, or paste a token address / $SYMBOL for a direct scan."
)

_SLOW_DOWN = "\U0001f422 <b>Slow down!</b>\n\nYou're sending messages too fast. Please wait {retry}s and try again."

_CHAT_DISABLED = (
    "\U0001f4ac <b>AI Chat is temporarily disabled</b>\n\n"
    "The chat feature is under maintenance. Commands like /scan, /wallet "
    "and /price still work \u2014 just send a contract address!"
)

_THINKING_GENERAL = (
    "\U0001f916 <b>On it...</b>\n\n\U0001f4ac <i>{question}</i>\n\n\U0001f50d Checking our intelligence database..."
)

_THINKING_ADDRESS = (
    "\U0001f916 <b>On it...</b>\n\n\U0001f4ac <i>{question}</i>\n\n"
    "\U0001f575\ufe0f Running token scan \u2192 checking exploit history \u2192 pulling live market data..."
)

_THINKING_INVESTIGATE = (
    "\U0001f916 <b>Deep diving...</b>\n\n\U0001f4ac <i>{question}</i>\n\n"
    "\U0001f4da Cross-referencing research collections + scanner data + news feeds..."
)

_THINKING_PRICE = (
    "\U0001f916 <b>Looking that up...</b>\n\n\U0001f4ac <i>{question}</i>\n\n"
    "\U0001f4b0 Fetching live prices + market data..."
)

_THINKING_NEWS = (
    "\U0001f916 <b>Checking the wire...</b>\n\n\U0001f4ac <i>{question}</i>\n\n"
    "\U0001f4f0 Pulling latest crypto news + market intelligence..."
)

_THINKING_SCAM = (
    "\U0001f916 <b>Checking the threat board...</b>\n\n\U0001f4ac <i>{question}</i>\n\n"
    "\U0001f6e1\ufe0f Searching scam databases + exploit patterns + known rug pulls..."
)

# "Investigate-style" intents get the research-collections placeholder instead
# of the generic DB-search one (matches the /investigate deep-dive framing).
_INVESTIGATE_INTENT_RE = re.compile(r"\b(investigat\w*|deep dive|forensic|trace this|dig into)\b", re.IGNORECASE)


# Price/market queries get a market-data message.
_PRICE_INTENT_RE = re.compile(r"\b(price|cost|worth|value|market cap|mcap|how much|trading at)\b", re.IGNORECASE)

# News queries get a news-feed message.
_NEWS_INTENT_RE = re.compile(
    r"\b(news|latest|headline|happened|today|breaking|update|what'?s going on)\b", re.IGNORECASE
)

# Scam/security queries get a threat-intel message.
_SCAM_INTENT_RE = re.compile(r"\b(scam|rug|honeypot|exploit|hack|ponzi|fraud|safe|trust)\b", re.IGNORECASE)


def _thinking_template(text: str, has_address: bool) -> str:
    """Pick the contextual thinking placeholder for a question.

    Address questions get the scanner-data message (scan context is
    pre-fetched and prepended to the prompt), investigate-style intents get
    the research-collections message, everything else gets the general
    intelligence-database message.
    """
    if has_address:
        return _THINKING_ADDRESS
    if _INVESTIGATE_INTENT_RE.search(text):
        return _THINKING_INVESTIGATE
    if _SCAM_INTENT_RE.search(text):
        return _THINKING_SCAM
    if _NEWS_INTENT_RE.search(text):
        return _THINKING_NEWS
    if _PRICE_INTENT_RE.search(text):
        return _THINKING_PRICE
    return _THINKING_GENERAL


_STILL_DIGGING = (
    "\U0001f916 <b>Still digging...</b>\n\n\u23f3 Cross-referencing exploit databases + checking live news..."
)


__all__ = [
    "CHUNK_MAX_CHARS",
    "MAX_ADDRESS_HINTS",
    "MAX_ANSWER_CHARS",
    "_CHAT_DISABLED",
    "_DEGENERATE_MESSAGE",
    "_FALLBACK_MESSAGE",
    "_INJECTION_MESSAGES",
    "_INTENT_RULES",
    "_INVESTIGATE_INTENT_RE",
    "_KNOWN_SCAM_WARNING",
    "_NEWS_INTENT_RE",
    "_PRICE_INTENT_RE",
    "_REDIRECT_MESSAGE",
    "_SCAM_INTENT_RE",
    "_SELF_DESCRIPTION_ANSWER",
    "_SELF_IMPROVEMENT_ANSWER",
    "_SLOW_DOWN",
    "_STILL_DIGGING",
    "_THINKING_ADDRESS",
    "_THINKING_GENERAL",
    "_THINKING_INVESTIGATE",
    "_THINKING_NEWS",
    "_THINKING_PRICE",
    "_THINKING_SCAM",
    "_beautify_answer",
    "_cap_answer",
    "_extract_addresses",
    "_format_scan_summary",
    "_grounding_sources_count",
    "_is_capability_question",
    "_is_deep_question",
    "_is_greeting_or_thanks",
    "_is_meaningful_question",
    "_is_section_header",
    "_is_self_improvement_question",
    "_markdown_bold_to_html",
    "_markdown_to_html",
    "_normalize_llm_markup",
    "_raw_html_emphasis_to_markdown",
    "_tables_to_lists",
    "_thinking_template",
    "_unescape_llm_entities",
    "is_degenerate_answer",
    "local_ai_response",
    "strip_think_blocks",
]
