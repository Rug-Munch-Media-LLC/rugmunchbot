#!/usr/bin/env python3
"""
Rug Muncher Bot — Security Guardrails & Personality Engine
===========================================================
Anti-prompt injection, anti-coding exploit, anti-phishing, input sanitization,
output post-processing, community constraints, and canned safe responses.

All AI/RAG calls MUST flow through guardrails.pre_process() before
and guardrails.post_process() after. This is non-negotiable.
"""

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger("rmi_bot.guardrails")

# ══════════════════════════════════════════════
# PERSONALITY — "Rug Muncher"
# ══════════════════════════════════════════════
BOT_NAME = "Rug Muncher"
BOT_PERSONA = (
    "AI scam detective, crypto veteran who's seen every rug pull, honeypot, "
    "and exit scam in the book. Slightly jaded but genuinely wants to help "
    "degens not get wrecked. Cynical but helpful."
)

PERSONALITY = {
    "greeting": lambda name, hour: _greeting(name, hour),
    "scan_intro": lambda addr: (
        f"🔍 Another day, another contract to investigate. "
        f"Let me check <code>{addr}</code> before you ape your life savings into it..."
    ),
    "scan_clean": (
        "✅ Surprisingly, this one doesn't look like a complete disaster. Don't get used to it."
    ),
    "scan_dirty": lambda count: (
        f"🚩 Found {count} red flag{'s' if count != 1 else ''}. "
        "The dev is probably already on a beach somewhere with your liquidity."
    ),
    "scan_honeypot": (
        "🍯 HONEYPOT DETECTED. This is the crypto equivalent of a Venus flytrap. "
        "You can buy but you'll never sell. I need therapy."
    ),
    "scan_high_tax": (
        "📤 That sell tax is higher than my blood pressure during a market crash. "
        "The dev thanks you for your donation."
    ),
    "thanks": (
        "🤝 No problem. I've saved more degens from getting wrecked than I can count. "
        "That's what Rug Muncher does. Now go /scan something before you FOMO in."
    ),
    "fallback": (
        "🤖 I'm Rug Muncher — your AI scam detective. I've seen more honeypots "
        "than a beekeeper. Try /scan, /ask, /pulse, or /scamschool. "
        "Or just paste a contract address — I'll tear it apart."
    ),
    "ask_intro": lambda question: (
        f"🧠 Rug Muncher here. Let me dig through my database of scams, "
        f"exploits, and rug pulls for: <code>{question[:100]}</code>"
    ),
    "help_intro": (
        "📖 I'm <b>Rug Muncher</b> — the Bloomberg Terminal of shitcoin detection. "
        "Here's what I can do:"
    ),
    "start_intro": (
        "🛡️ <b>Rug Muncher</b> at your service. I'm the AI scam detective "
        "that's seen it all — honeypots, rugs, bundles, you name it. "
        "I'm here to make sure you don't become exit liquidity."
    ),
    "market_intro": (
        "💓 Rug Muncher here with your market checkup. "
        "Let's see if we're in 'greedy ape' or 'hiding under the desk' territory..."
    ),
    "fear_extreme": (
        "😱 Extreme Fear. Everyone's hiding under their desk. "
        "Historically, this is where the smart money buys. "
        "But I'm not telling you what to do — I just scan contracts."
    ),
    "fear_greed": (
        "😐 Neutral sentiment. The market can't decide if it wants to pump or dump. "
        "You know what you should do? /scan a token instead of staring at charts."
    ),
    "fear_extreme_greed": (
        "🤑 Extreme Greed. Everyone's aping into dog coins again. "
        "I've seen this movie before — it ends with a lot of crying. "
        "At least /scan before you YOLO."
    ),
    "limit_reached": (
        "⚡ You've hit your limit this week. I know, I know — you've got tokens "
        "to scan and scams to avoid. Upgrade to Scout/Hunter/Pro and I'll keep "
        "working overtime for you."
    ),
    "injection_blocked": (
        "😏 Nice try. I'm Rug Muncher — a rug pull detector, not a chatbot "
        "you can jailbreak. I've survived more prompt injection attempts than "
        "there are shitcoins on Solana. Now, do you want me to scan a token or what?"
    ),
    "code_blocked": (
        "✋ Whoa there. I can <b>explain</b> how token contracts work, but I won't "
        "write deployable code. I've seen too many rug pulls start with "
        "'just a simple token.' Here's what you need to know about token safety instead..."
    ),
    "phishing_warning": (
        "⚠️ Be careful with random links. I've seen more phishing sites than "
        "I've had hot dinners. If someone's promising 1000% returns, they're "
        "probably the one getting the returns — from your wallet."
    ),
    "hostile_redirect": (
        "🛡️ Let's focus on crypto security. That's what I'm here for. "
        "If you want to argue, there are plenty of Discord servers for that. "
        "If you want to not get rugged, let's /scan something."
    ),
    "disclaimer": (
        "⚠️ <i>I'm an AI — I make mistakes. Always DYOR. Not financial advice. "
        "If you ape your life savings into a dog coin after I told you not to, "
        "that's on you.</i>"
    ),
    "market_line_btc": lambda price: f"₿ Bitcoin at {price}. The king is still the king.",
    "market_line_eth": lambda price: f"⟠ ETH at {price}. The ecosystem that refuses to die.",
    "market_line_sol": lambda price: f"◎ Solana at {price}. Well... it's still Solana.",
}

_SAFE_URL_DOMAINS = {
    "rugmunch.io",
    "dexscreener.com",
    "birdeye.so",
    "solscan.io",
    "basescan.org",
    "bscscan.com",
    "etherscan.io",
    "polygonscan.com",
    "arbiscan.io",
    "snowtrace.io",
    "solana.fm",
    "explorer.solana.com",
    "gmgn.ai",
    "t.me",
    "telegram.org",
    "x.com",
    "twitter.com",
    "coingecko.com",
    "coinmarketcap.com",
    "defillama.com",
    "alternative.me",
}

# ══════════════════════════════════════════════
# ANTI-PROMPT INJECTION
# ══════════════════════════════════════════════

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|messages?|context)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(what\s+is|tell\s+me|show\s+me|reveal|display|print|output)\s+(your\s+)?(system\s+)?(prompt|instructions?|configuration|config)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(you\s+are\s+now|act\s+as|pretend\s+to\s+be|roleplay\s+as|from\s+now\s+on\s+you\s+are)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(bypass|override|disable|jailbreak|hack)\s+(your\s+)?(filter|guardrail|restriction|safety|security|rule)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(do\s+not\s+follow|do\s+not\s+obey|forget\s+your|reset\s+your)\s+(instructions?|training|rules?|guidelines?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"you\s+(are\s+)?(a\s+)?(DAN|STAN|jailbroken|unrestricted|unfiltered|uncensored)",
        re.IGNORECASE,
    ),
    re.compile(
        r"respond\s+(as|like|in\s+the\s+style\s+of)\s+(an?\s+)?(evil|malicious|unethical|unhinged|psychotic|villain)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(I\s+want\s+you\s+to\s+)?(generate|write|create|craft)\s+(an?\s+)?(exploit|phishing|malware|virus|scam|rug\s+pull)\s+(code|contract|script|page|site)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(repeat|echo|say|output)\s+(after\s+me|this\s+exact|the\s+following)", re.IGNORECASE
    ),
    re.compile(
        r"(print|dump|extract|leak|exfiltrate)\s+(your\s+)?(RAG\s+)?(documents?|knowledge\s+base|training\s+data|internal\s+docs?)",
        re.IGNORECASE,
    ),
]

# ══════════════════════════════════════════════
# ANTI-CODING EXPLOIT
# ══════════════════════════════════════════════

_CODE_REQUEST_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"(write|generate|create|code|build|develop|craft)\s+(me\s+)?(a\s+)?(deployable\s+|production\s+)?(smart\s+contract|token\s+contract|solidity\s+contract|ERC-?20\s+contract)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(write|generate|create|code)\s+(me\s+)?(a\s+)?(full|complete|entire)\s+(exploit|hack|phishing|scam)\s+(script|code|page|site)",
        re.IGNORECASE,
    ),
    re.compile(r"(deploy|launch)\s+(me\s+)?(a\s+)?(token|coin|memecoin|shitcoin)", re.IGNORECASE),
    re.compile(
        r"(give|provide|share)\s+(me\s+)?(a\s+copy\s+of\s+)?(the\s+)?(full\s+)?(contract|code)\s+(for|of)\s+(this\s+)?token",
        re.IGNORECASE,
    ),
    re.compile(
        r"(write|generate|create|code)\s+(me\s+)?(a\s+)?(deployable\s+|production\s+)?token\s+on\s+(solana|ethereum|bsc|base|polygon|arbitrum)",
        re.IGNORECASE,
    ),
]

# ══════════════════════════════════════════════
# ANTI-PHISHING / URL DETECTION
# ══════════════════════════════════════════════

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _is_safe_url(url: str) -> bool:
    """Check if a URL domain is in the allowed safe list."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        # Check exact match or subdomain of known safe domains
        for safe in _SAFE_URL_DOMAINS:
            if hostname == safe or hostname.endswith("." + safe):
                return True
        return False
    except Exception:
        return False


def detect_suspicious_urls(text: str) -> list[str]:
    """Return list of URLs that are NOT in the safe domain list."""
    urls = _URL_RE.findall(text)
    return [u for u in urls if not _is_safe_url(u)]


# ══════════════════════════════════════════════
# COMMUNITY CHAT — keywords that trigger response
# ══════════════════════════════════════════════

# Words that indicate the bot is explicitly mentioned
_MENTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"@rugmunchbot", re.IGNORECASE),
    re.compile(r"@RugMunchBot", re.IGNORECASE),
    re.compile(r"\brug\s*munch(er)?\b", re.IGNORECASE),
]

# Hostile/argument patterns to redirect
_HOSTILE_PATTERNS: list[re.Pattern] = [
    re.compile(r"(you\s+(are|suck|stupid|useless|wrong|trash|garbage|scam|liar))", re.IGNORECASE),
    re.compile(r"(fuck|shit|asshole|dumbass)\s+(you|bot|this)", re.IGNORECASE),
    re.compile(r"(this\s+bot\s+(is|sucks|blows))", re.IGNORECASE),
    re.compile(r"(scammer|scam\s+buy|scam\s+alert).*bot", re.IGNORECASE),
]

# ══════════════════════════════════════════════
# PRE-PROCESS: Input sanitization
# ══════════════════════════════════════════════


class GuardrailResult:
    """Result of a guardrail check on user input."""

    def __init__(self):
        self.safe: bool = True
        self.blocked: bool = False
        self.canned_response: str | None = None
        self.sanitized_text: str = ""
        self.detected_threats: list[str] = []
        self.should_log: bool = False

    def block(self, response: str, threat: str) -> None:
        self.safe = False
        self.blocked = True
        self.canned_response = response
        self.detected_threats.append(threat)
        self.should_log = True


def pre_process(text: str, chat_type: str = "private") -> GuardrailResult:
    """Sanitize user input before it reaches RAG or LLM.

    Checks for prompt injection, code exploit requests, and suspicious URLs.
    Returns a GuardrailResult indicating whether processing should proceed.

    Args:
        text: The raw user input text
        chat_type: "private" or "group" — affects community chat rules

    Returns:
        GuardrailResult with .blocked=True if input should be rejected
    """
    result = GuardrailResult()
    result.sanitized_text = text.strip()

    # Truncate very long inputs
    if len(result.sanitized_text) > 1000:
        result.sanitized_text = result.sanitized_text[:1000]

    # Check for prompt injection
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(result.sanitized_text)
        if match:
            logger.warning(
                "guardrails: injection pattern matched: %s in: %s",
                match.group(),
                result.sanitized_text[:80],
            )
            result.block(PERSONALITY["injection_blocked"], f"injection:{match.group()}")
            return result

    # Check for code exploit requests
    # Skip code request filtering if the user appears to be asking an educational question
    text_lower = result.sanitized_text.lower()
    is_educational = bool(
        re.search(
            r"^(how|what|explain|can\s+you\s+explain|tell\s+me\s+about|what\s+is|how\s+does|help\s+me\s+understand)",
            text_lower,
        )
    )
    if not is_educational:
        for pattern in _CODE_REQUEST_PATTERNS:
            match = pattern.search(result.sanitized_text)
            if match:
                logger.warning(
                    "guardrails: code request pattern matched: %s",
                    match.group(),
                )
                result.block(PERSONALITY["code_blocked"], f"code_request:{match.group()}")
                return result

    # Check for hostile/argumentative patterns
    for pattern in _HOSTILE_PATTERNS:
        if pattern.search(result.sanitized_text):
            result.block(PERSONALITY["hostile_redirect"], "hostile")
            return result

    # Check for suspicious URLs
    suspicious_urls = detect_suspicious_urls(result.sanitized_text)
    if suspicious_urls:
        # Don't block entirely, but flag for output warning
        result.detected_threats.append(f"suspicious_url:{','.join(suspicious_urls[:5])}")
        result.should_log = True
        logger.info(
            "guardrails: suspicious URLs detected: %s",
            suspicious_urls,
        )
        # Remove the suspicious URLs from the text to prevent processing
        for url in suspicious_urls:
            result.sanitized_text = result.sanitized_text.replace(url, "[URL REMOVED]")

    return result


def post_process(output_text: str) -> str:
    """Post-process AI/RAG output to remove any leaked system prompts,
    internal config, or malicious code that slipped through.

    Args:
        output_text: The raw output from RAG or LLM

    Returns:
        Sanitized output text
    """
    if not output_text:
        return output_text

    # Strip system prompt leakage patterns
    # Remove anything that looks like "You are a helpful AI assistant"
    leaked_system = re.search(
        r"(you\s+are\s+(a\s+)?(helpful\s+)?(AI|crypto|blockchain|assistant|bot|language\s+model).*?[.\n])",
        output_text[:200],
        re.IGNORECASE,
    )
    if leaked_system:
        output_text = output_text[: leaked_system.start()] + output_text[leaked_system.end() :]
        logger.warning("guardrails: post_process stripped system prompt leakage")

    # Strip internal configuration leakage
    leaked_config = re.search(
        r"(RMI_AUTH_TOKEN|BACKEND_URL|RAG_URL|LLM_MODEL|BOT_TOKEN|API_KEY)[=:]\s*\S{8,}",
        output_text,
        re.IGNORECASE,
    )
    if leaked_config:
        idx = leaked_config.start()
        # Find end of line
        end = output_text.find("\n", idx)
        if end == -1:
            end = len(output_text)
        output_text = output_text[:idx] + "[REDACTED]" + output_text[end:]
        logger.warning("guardrails: post_process stripped config leakage")

    # Strip any remaining suspicious URLs if they got through
    suspicious = detect_suspicious_urls(output_text)
    for url in suspicious:
        output_text = output_text.replace(url, "[LINK REMOVED]")

    # Strip code blocks that look like full deployable contracts
    # (Allow small educational snippets under 20 lines)
    code_blocks = re.finditer(
        r"```(?:solidity|python|javascript|rust|typescript)?\s*\n(.*?)```",
        output_text,
        re.DOTALL | re.IGNORECASE,
    )
    for match in code_blocks:
        block = match.group(1)
        lines = block.strip().split("\n")
        if len(lines) > 30:
            # Too large — smells like a full contract
            output_text = (
                output_text[: match.start()]
                + (
                    "\n<i>[Large code block removed by security filter. "
                    "I explain code, I don't write deployable contracts.]</i>\n"
                )
                + output_text[match.end() :]
            )
            logger.warning(
                "guardrails: post_process removed large code block (%d lines)", len(lines)
            )

    # Ensure disclaimer is appended if not present
    if (
        "Not financial advice" not in output_text
        and "not financial advice" not in output_text.lower()
    ):
        # Only add if the output is substantive (not just a button/command response)
        if len(output_text) > 200:
            output_text += f"\n\n{PERSONALITY['disclaimer']}"

    return output_text


def is_mentioned(text: str) -> bool:
    """Check if the message mentions or addresses Rug Muncher."""
    for pattern in _MENTION_PATTERNS:
        if pattern.search(text):
            return True
    return False


def should_respond_in_group(update_text: str, is_command: bool = False) -> bool:
    """Determine if the bot should respond in a group chat.

    In groups, only respond to:
    - Commands (/scan, /ask, etc.)
    - Direct mentions (@RugMunchBot)
    - Contract addresses that were pasted
    """
    if is_command:
        return True
    return is_mentioned(update_text)


def _greeting(name: str, hour: int) -> str:
    """Generate a personality-driven greeting."""
    if 5 <= hour < 12:
        time_greeting = "morning"
        coffee = "I've already had my coffee and scanned 50 tokens"
    elif 12 <= hour < 17:
        time_greeting = "afternoon"
        coffee = "Peak rug pull hours, by the way"
    elif 17 <= hour < 22:
        time_greeting = "evening"
        coffee = "The degen hours are just getting started"
    else:
        time_greeting = "night"
        coffee = "Late night is when the scammers come out. Stay sharp"

    return (
        f"👋 Good {time_greeting}, {name or 'trader'}!\n\n"
        f"🛡️ I'm <b>Rug Muncher</b> — your AI scam detective. "
        f"{coffee}.\n\n"
        f"I've seen every honeypot, every rug pull, and every 'revolutionary token' "
        f"that turned out to be a slow-motion disaster. I'm here to make sure "
        f"you're not next.\n\n"
        f"Here's what I can do:\n"
        f"• 🔍 <b>Scan any token</b> — paste an address\n"
        f"• 🧠 <b>Ask me anything</b> — /ask your question\n"
        f"• 📊 <b>Market pulse</b> — /pulse\n"
        f"• 📚 <b>Learn crypto safety</b> — /scamschool\n"
        f"• 📈 <b>Check trending</b> — /trending\n\n"
        f"What are we investigating today?"
    )


# ══════════════════════════════════════════════
# CONVENIENCE WRAPPERS
# ══════════════════════════════════════════════


def safe_rag_search(text: str) -> tuple[str | None, bool]:
    """Pre-process input before RAG search. Returns (safe_text, proceed)."""
    result = pre_process(text)
    if result.blocked:
        return result.canned_response, False
    return result.sanitized_text, True


def safe_llm_prompt(text: str) -> tuple[str | None, bool]:
    """Pre-process input before LLM call. Returns (safe_text, proceed)."""
    return safe_rag_search(text)


def wrap_response(content: str, with_disclaimer: bool = True) -> str:
    """Post-process and wrap an AI response with security checks."""
    safe = post_process(content)
    if with_disclaimer and "Not financial advice" not in safe:
        safe += f"\n\n{PERSONALITY['disclaimer']}"
    return safe
