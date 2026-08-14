"""Pure helpers for the free-text AI chat pipeline (chat_ai.py).

Extracted from ``handlers/chat_ai.py`` during the Batch 11 de-godding pass:
formatting / prompt-building / response-parsing helpers that carry no
``Update``/``Context`` state and no handler registration. chat_ai.py re-imports
everything it needs from here; behavior is byte-for-byte identical.
"""

from __future__ import annotations

import re
from typing import Any

from rugmunchbot.formatters import footer_links, thin_sep
from rugmunchbot.handlers.chat_ai._parts import _is_fence_line

CHUNK_MAX_CHARS = 3800  # Telegram hard limit is 4096; leave headroom for escape expansion
MAX_ADDRESS_HINTS = 2  # cap address-triggered scans per message


# ══════════════════════════════════════════════
# v10 RESPONSE BEAUTIFIER (see _beautify_answer / _markdown_bold_to_html)
# Section emojis match the persona's RESPONSE FORMATTING block (prompts.py).
# A line that STARTS with one of these and is short is treated as a section
# header: normalized to **emoji Title**, with thin_sep() inserted beneath it.
# ══════════════════════════════════════════════
_SECTION_EMOJIS: frozenset[str] = frozenset(
    [
        "\U0001f4ca",  # 📊 statistics
        "\U0001f4c8",  # 📈 chart increasing
        "\U0001f4c9",  # 📉 chart decreasing
        "\U0001f50d",  # 🔍 magnifying glass
        "\U0001f6e1\ufe0f",  # 🛡️ shield
        "\U0001f40b",  # 🐋 whale
        "\U0001f464",  # 👤 bust
        "\U0001f4b0",  # 💰 money bag
        "\U0001f525",  # 🔥 fire
        "\U0001f6a8",  # 🚨 siren
        "\u2705",  # ✅ check
        "\u26a0\ufe0f",  # ⚠️ warning
        "\U0001f4ac",  # 💬 speech balloon
        "\U0001f4f0",  # 📰 newspaper
        "\U0001f465",  # 👥 people
        "\U0001f680",  # 🚀 rocket
    ]
)
_SECTION_HEADER_MAX = 80  # longer emoji-prefixed lines are content, not headers
_BOLD_RE = re.compile(r"\*{2,3}([^*\n]+?)\*{2,3}")


# ══════════════════════════════════════════════
# ADDRESS DETECTION (free-text search, mirrors app/routers/chat.py)
# ══════════════════════════════════════════════
_EVM_ADDR_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_SOL_ADDR_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


def _plausible_solana(addr: str) -> bool:
    """Base58 address-likeness: real Solana addresses almost always mix digits
    or uppercase with lowercase, so this filters word-shaped false positives
    ("supercalifragilisticexpialidocious") without dropping genuine tokens."""
    return any(ch.isdigit() or ch.isupper() for ch in addr)


def _extract_addresses(text: str) -> list[str]:
    """Return token addresses (EVM 0x... / Solana base58) found in free text.

    Capped at ``MAX_ADDRESS_HINTS`` so a single message cannot trigger a scan
    storm. Solana matches that overlap an EVM match are skipped.
    """
    addresses: list[str] = []
    for m in _EVM_ADDR_RE.finditer(text):
        addresses.append(m.group(0))
        if len(addresses) >= MAX_ADDRESS_HINTS:
            return addresses
    for m in _SOL_ADDR_RE.finditer(text):
        addr = m.group(0)
        if addr not in addresses and _plausible_solana(addr):
            addresses.append(addr)
        if len(addresses) >= MAX_ADDRESS_HINTS:
            break
    return addresses


# ══════════════════════════════════════════════
# SCANNER CONTEXT (address questions get a risk summary prepended)
# ══════════════════════════════════════════════


def _format_scan_summary(address: str, data: dict[str, Any]) -> str | None:
    """Compact single-line risk summary used to ground an address question.

    Returns ``None`` when the scanner returned no risk score so the caller
    falls through to a plain question.
    """
    risk = data.get("risk_score")
    if risk is None:
        return None
    flags = [f"risk_score={int(risk)}"]
    for key in ("is_honeypot", "is_mintable", "is_renounced"):
        val = data.get(key)
        if val is not None:
            flags.append(f"{key}={bool(val)}")
    liq = data.get("liquidity_usd") or 0
    if liq:
        flags.append(f"liquidity=${float(liq):,.0f}")
    holders = data.get("holders")
    if holders:
        flags.append(f"holders={holders}")
    return f"Context: token {address} scan shows {', '.join(flags)}"


def _last_sentence_boundary(text: str, limit: int) -> int:
    """Index just past the last sentence-ending punctuation before ``limit``.

    Returns -1 when no sentence boundary exists inside the window. Used by
    :func:`_split_oversized` so hard splits land at sentence ends instead of
    mid-sentence ("split at natural section boundaries, not mid-sentence").
    """
    best = -1
    for sep_ in (". ", "? ", "! "):
        idx = text.rfind(sep_, 0, limit)
        if idx >= 0:
            best = max(best, idx + len(sep_))
    if best > limit:
        return limit  # boundary punctuation ends exactly at the window edge
    return best


def _split_oversized(paragraph: str, max_len: int) -> list[str]:
    """Hard-split a single oversized paragraph — prefer sentence boundaries.

    Falls back to the last word boundary, then an exact cut, so a cut never
    lands mid-word. Sentence-boundary preference keeps chunks readable.
    """
    if len(paragraph) <= max_len:
        return [paragraph]
    parts: list[str] = []
    remaining = paragraph
    while len(remaining) > max_len:
        cut = _last_sentence_boundary(remaining, max_len)
        if cut < max_len // 2:
            cut = remaining.rfind(" ", 0, max_len)  # no sentence end — word boundary
        if cut < max_len // 2:
            cut = max_len  # no good boundary — cut exactly
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _chunk_text(text: str, max_len: int = CHUNK_MAX_CHARS) -> list[str]:
    """Split ``text`` into Telegram-sized chunks on paragraph boundaries.

    Operates on the RAW (pre-escape) text: paragraphs are the safest split
    points and no chunk ever cuts through an HTML entity that escaping would
    later introduce (e.g. ``&lt;``). New emoji section headers (v10) always
    start a fresh chunk when the current one is non-empty, so headers never
    dangle at the tail of a previous section.
    """
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        for piece in _split_oversized(paragraph, max_len):
            if current and _is_section_header(piece):
                # v10: prefer section boundaries — a new emoji header leads a
                # new chunk so it is never glued to the previous section.
                chunks.append(current)
                current = ""
            elif current and len(current) + len(piece) + 2 > max_len:
                chunks.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


# ══════════════════════════════════════════════
# v10 RESPONSE BEAUTIFIER
# Runs on the RAW LLM answer BEFORE chunking/escaping: normalizes emoji
# section headers, inserts thin separators under them, and keeps markdown
# **bold** markers for the post-escape conversion in _send_answer.
# ══════════════════════════════════════════════


def _is_section_header(line: str) -> bool:
    """True for a short line that begins with a section emoji (v10 header).

    A line starting with an emoji but longer than ``_SECTION_HEADER_MAX`` is
    prose, not a header — e.g. "📊 This token's chart broke out of a wedge
    that's been building since March" stays content. Prefix matching handles
    multi-code-point emojis (🛡️ = U+1F6E1 + VS16).
    """
    core = line.strip().lstrip("*").strip()
    if not core or len(core) > _SECTION_HEADER_MAX:
        return False
    return any(core.startswith(emoji) for emoji in _SECTION_EMOJIS)


def _unwrap_bold(line: str) -> str:
    """Strip a balanced ``**...**``/``***...***`` wrapper (or leading `*`)."""
    stripped = line.strip()
    m = re.match(r"^\*{2,3}(.+?)\*{2,3}$", stripped)
    if m:
        return m.group(1).strip()
    return re.sub(r"^\*{1,3}", "", stripped).strip()


def _beautify_answer(answer: str) -> str:
    """v10 beautifier — normalize headers + insert thin separators (raw text).

    - Lines that look like section headers (start with a section emoji,
      short) are normalized to ``**emoji Title**`` on their own line, with a
      blank line before them so the chunker sees a clean section boundary.
    - ``thin_sep()`` is inserted beneath every header for visual rhythm
      (📊 header / ──── / content, like the /scan report blocks).
    - All other lines are preserved verbatim (code blocks, lists, spacing).
    Markdown ``**bold**`` is left in place for :func:`_markdown_bold_to_html`
    which runs AFTER escaping in :func:`_send_answer`.
    """
    lines = answer.split("\n")
    out: list[str] = []
    prev_empty = True
    for raw_line in lines:
        if not raw_line.strip():
            out.append("")
            prev_empty = True
            continue
        if _is_section_header(raw_line):
            if not prev_empty:
                out.append("")
            out.append(f"**{_unwrap_bold(raw_line)}**")
            out.append(thin_sep())
            prev_empty = False
            continue
        out.append(raw_line)
        prev_empty = False
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _markdown_bold_to_html(text: str) -> str:
    """Convert ``**bold**`` / ``***bold***`` to ``<b>bold</b>``.

    MUST run AFTER ``html.escape()`` so the wrapped content is already
    entity-safe and only the asterisk pairs are replaced — LLM-supplied HTML
    is never re-introduced. Unbalanced asterisks pass through untouched.
    """
    return _BOLD_RE.sub(r"<b>\1</b>", text)


# ══════════════════════════════════════════════
# MARKDOWN TABLE → LIST FALLBACK
# Telegram HTML has no table element; a raw markdown pipe table would render
# as a wall of literal ``|`` characters. Convert table blocks to compact
# bullet lists BEFORE escaping. Fenced code blocks are never touched.
# ══════════════════════════════════════════════
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")


def _split_table_row(row: str) -> list[str]:
    """Split a markdown table row into trimmed cells (drop outer pipes)."""
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _is_table_block(rows: list[str]) -> bool:
    """True when consecutive pipe rows look like a markdown table.

    A separator row (``|---|``) is the definitive signal; a run of 3+ pipe
    rows without one (some LLMs skip the separator) is treated as a table
    too. Single stray pipe lines are left alone.
    """
    if len(rows) < 2:
        return False
    if any(_TABLE_SEP_RE.match(r) for r in rows):
        return True
    return len(rows) >= 3


def _render_table_block(rows: list[str]) -> list[str]:
    """Render a markdown table block as compact bullet lines (raw text).

    The header row (first row, when a separator row is present) becomes a
    ``**a · b**`` line so the bold conversion renders it as a header; each
    data row becomes ``• a · b · c``. The ``|---|`` separator row is
    dropped. ``·`` (U+00B7) keeps rows on one line without the pipe-wall
    look of the original table.
    """
    has_separator = len(rows) >= 2 and bool(_TABLE_SEP_RE.match(rows[1]))
    rendered: list[str] = []
    for idx, row in enumerate(rows):
        if _TABLE_SEP_RE.match(row):
            continue
        cells = _split_table_row(row)
        if not cells or any(not c for c in cells):
            continue
        if has_separator and idx == 0:
            rendered.append(f"**{' · '.join(cells)}**")
        else:
            rendered.append(f"\u2022 {' · '.join(cells)}")
    return rendered


def _tables_to_lists(text: str) -> str:
    """Convert markdown table blocks to compact bullet lists (raw text).

    Detect table blocks (consecutive ``|`` rows, with or without a
    ``|---|`` separator row) and render each row as ``• a · b · c`` with
    the header bolded. Fenced code blocks are never touched: pipe-shaped
    lines inside ````` ``` ```` stay verbatim.
    """
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if _is_fence_line(line):
            in_fence = not in_fence
        if in_fence or not _TABLE_ROW_RE.match(line):
            out.append(line)
            i += 1
            continue
        run: list[str] = []
        while i < n and _TABLE_ROW_RE.match(lines[i]):
            run.append(lines[i])
            i += 1
        if not _is_table_block(run):
            out.extend(run)
            continue
        out.extend(_render_table_block(run))
    return "\n".join(out)


# ══════════════════════════════════════════════
# GIBBERISH GATE (was handlers/message.py tier-1b gate)
# ══════════════════════════════════════════════


def _is_meaningful_question(text: str) -> bool:
    """Return False for gibberish / one-liners not worth an LLM call."""
    stripped = text.strip()
    if len(stripped) < 4:
        return False
    words = re.findall(r"[A-Za-z]{3,}", stripped)
    if not words:
        return False
    if len(stripped) > 0 and sum(1 for ch in stripped if ch.isalpha()) / len(stripped) < 0.35:
        return False
    # Repeated single word / character spam: "asdfasdfasdf", "lllll"
    return not (len(set(words)) == 1 and len(words) > 4)


# ══════════════════════════════════════════════
# ANSWER POST-PROCESSING GUARDS (think-strip + degenerate-output)
# Pure, unit-testable; applied in chat_ai_response before the answer is
# stored/sent so the user and the chat history never see a reasoning chain
# or a degenerate token-repetition loop.
# ══════════════════════════════════════════════


def strip_think_blocks(text: str) -> str:
    """Strip ``<think>...</think>`` reasoning blocks from an LLM answer.

    Local models (deepseek-analysis) emit visible reasoning chains and the
    backend / zen / local providers can all surface them. Handles BOTH
    complete blocks and unterminated blocks truncated by ``max_tokens`` —
    exactly like ``ai_router._strip_think_tags`` (an unclosed ``<think>``
    drops everything from it onward). Idempotent; safe on plain text.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


_DEGENERATE_REPETITION_RE = re.compile(r"^(.{2,40}?)\1{2,}$", re.DOTALL)


def is_degenerate_answer(text: str) -> bool:
    """True when an LLM answer carries no usable content.

    Covers empty/whitespace, bare bullets/dashes, punctuation/emoji-only
    output, and token-repetition loops ("ioneerioneerioneer...") the local
    models degenerate into. Mirrors ``ai_router._is_degenerate`` and adds
    the repetition-loop detector — the loop carries alphanumerics, so the
    backend's punctuation-only check never catches it.
    """
    stripped = (text or "").strip()
    if not stripped:
        return True
    if not any(c.isalnum() for c in stripped):
        return True
    return bool(_DEGENERATE_REPETITION_RE.match(stripped))


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
    "wallet-secret-harvest": (
        "Never share your seed phrase or private keys with anyone — including bots."
    ),
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
# SELF-IMPROVEMENT / DEVELOPER QUESTIONS
# "what capabilities could I add that would help you do your job better" is
# a REAL question — NOT a request for the command menu. The generic
# capability rule used to over-match "capabilities" and dump the menu for
# any capability mention, so self-improvement questions get their own
# specific answer (capabilities already present + genuine improvement gaps).
# ══════════════════════════════════════════════

_SELF_IMPROVEMENT_RE = re.compile(
    r"(?:"
    r"\bwould help you\b"
    r"|\bcould you use\b"
    r"|\bif i added\b"
    r"|\bas your developer\b"
    r"|\bwhat do you need\b"
    r"|\bhow (?:could|can) (?:you improve|i improve you|i make you better)\b"
    r"|\bwhat would make you better\b"
    r"|\bwhat (?:capabilit\w*|features|tools|data) (?:should|could) i add\b"
    r"|\bwhat (?:capabilit\w*|features|tools|data) could you use\b"
    r"|\b(?:capabilit\w*|features|tools|data) you could use\b"
    r"|\bwhat (?:would|could) (?:help|improve|make) you\b"
    r"|\bwhat (?:could|should) i add\b"
    r"|\bto perform better\b"
    r"|\bperform your job better\b"
    r"|\bdo your job better\b"
    r"|\bimprove (?:yourself|the bot)\b"
    r")",
    re.IGNORECASE,
)

_SELF_IMPROVEMENT_ANSWER = (
    "\U0001f6e0\ufe0f <b>What would genuinely help me</b>\n\n"
    "Love this question! I already run on a solid stack \u2014 token "
    "security scans, wallet forensics, cross-chain tracing, live prices, "
    "whale alerts, and a 91K+ doc intel library across 25+ chains. But if "
    "you're asking what would make me <i>better</i> at my job:\n\n"
    "\U0001f4c8 <b>Real-time price &amp; orderbook feeds</b> \u2014 "
    "sub-second prices and depth data so my market answers never go stale.\n"
    "\U0001f4cb <b>Full on-chain history</b> \u2014 complete transfer and "
    "trade history for any token or wallet, so traces and funding paths go "
    "deeper than recent activity.\n"
    "\U0001f465 <b>More labeled wallet data</b> \u2014 exchanges, funds, "
    "scammer addresses and airdrop farmers, so cluster analysis names the "
    "players instead of just linking them.\n"
    "\U0001f9e0 <b>A stronger LLM</b> \u2014 bigger context and better "
    "reasoning means sharper answers on complex multi-step investigations.\n"
    "\U0001f4f0 <b>Live news &amp; webhooks</b> \u2014 push feeds for "
    "exploits, launches and whale moves, so I can flag threats the moment "
    "they happen instead of waiting for someone to ask.\n\n"
    "Each of those turns me from reactive to proactive \u2014 faster, "
    "deeper, safer intel for the whole community."
    f"{footer_links()}"
)


# ══════════════════════════════════════════════
# TIER-2 LOCAL INTENT MATCHING (ported from handlers/message.py)
# Canned, zero-backend answers for greetings / feature intents. Used for
# gibberish that is not worth an LLM call and as an offline fallback when the
# backend chat is unavailable.
# ══════════════════════════════════════════════

_INTENT_RULES: list[tuple[re.Pattern[str], str]] = [
    # Self-improvement questions MUST win over the capability menu (and over
    # incidental keyword matches like "scan"/"news" in e.g. "what would help
    # you scan tokens better"), so this rule is registered FIRST.
    (
        _SELF_IMPROVEMENT_RE,
        _SELF_IMPROVEMENT_ANSWER,
    ),
    (
        re.compile(
            r"\b(scan|check|audit|rug|scam|honeypot|safu|drain|phish|malicious|"
            r"dangerous|secure|risky|safe(ty)?|is this safe|is it safe|trustworthy)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f50d <b>Token Security Scan</b>\n\n"
            "Use <code>/scan &lt;address&gt;</code> to run a full security audit. "
            "I check for:\n"
            "\u2022 \U0001f36f Honeypot detection\n"
            "\u2022 \U0001f3c3 Rug pull risks\n"
            "\u2022 \U0001f4e6 Bundle / wash trading\n"
            "\u2022 \U0001f9db Dev wallet history\n"
            "\u2022 \U0001f4ca Liquidity & holder analysis\n\n"
            "Just paste any contract address and I'll auto-detect it!"
        ),
    ),
    (
        re.compile(
            r"\b(price|chart|worth|value|how much|cost|mc[aap]+|market cap|volume|"
            r"liquidity|pump|dump|buy|sell|token price|usd)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f4ca <b>Price & Market Data</b>\n\n"
            "Use <code>/quick &lt;symbol&gt;</code> or <code>$TOKEN</code> for instant prices.\n"
            "Use <code>/chart &lt;address&gt;</code> for technical analysis charts.\n"
            "Use <code>/scan &lt;address&gt;</code> for full market data.\n\n"
            "\U0001f4b0 Real-time market data for any token."
        ),
    ),
    (
        re.compile(
            r"\b(wallet|portfolio|balance|holdings|my token|my coin|bag|"
            r"hodl|fund|money)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f9db <b>Wallet Analysis</b>\n\n"
            "Use <code>/wallet &lt;address&gt;</code> to analyze any wallet.\n"
            "Use <code>/portfolio</code> to view your tracked wallets.\n\n"
            "I show:\n"
            "\u2022 \U0001f4b0 Token balances & values\n"
            "\u2022 \U0001f504 Recent transaction history\n"
            "\u2022 \u26a0\ufe0f Risk metrics & scam checks\n"
            "\u2022 \U0001f517 Counterparty connections"
        ),
    ),
    (
        re.compile(
            r"\b(compare|vs |versus|better than|which (is|one)|difference)\b", re.IGNORECASE
        ),
        (
            "\u2696\ufe0f <b>Token Comparison</b>\n\n"
            "Use <code>/compare &lt;addr1&gt; &lt;addr2&gt;</code> to pit two tokens "
            "side-by-side.\n\n"
            "I'll show you both risk scores, prices, liquidity, and holder metrics "
            "in one view!"
        ),
    ),
    (
        re.compile(
            r"\b(trending|hot|popular|what to buy|best coin|moon|gem|"
            r"next 100x|top token|gainers|movers)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f525 <b>Trending Tokens</b>\n\n"
            "Check <code>/trending</code> for the hottest tokens right now.\n"
            "I show real-time data on:\n"
            "\u2022 \U0001f4c8 Top gainers\n"
            "\u2022 \U0001f4a7 Highest volume\n"
            "\u2022 \U0001f195 New pairs\n"
            "\u2022 \U0001f3e0 Most holders"
        ),
    ),
    (
        re.compile(
            r"\b(alert|notify|watch|track|monitor|notification|remind|follow)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f514 <b>Price Alerts</b>\n\n"
            "Use <code>/watch &lt;address&gt;</code> to add tokens to your watchlist.\n"
            "Use <code>/alerts</code> to manage your price alerts.\n\n"
            "\U0001f4cc <b>Scout+</b> tier gets real-time push alerts for price moves, "
            "large transactions, and security events."
        ),
    ),
    (
        re.compile(
            r"\b(mev|sandwich|frontrun|backrun|protect(ion)? (trade|swap)|"
            r"safe swap|slippage|gas optimal|mev blocker|flashbot)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f6e1\ufe0f <b>MEV Protection</b>\n\n"
            "Use <code>/mev &lt;token_address&gt;</code> to check sandwich attack risk "
            "and get safe swap parameters:\n"
            "• Frontrunning risk score\n"
            "• Recommended slippage tolerance\n"
            "• Optimal gas settings\n"
            "• MEV-blocker routing suggestion\n\n"
            "<i>Free during beta — protect your trades from MEV bots.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(impersonat|fake token|copycat|clone token|pretending to be|"
            r"fake version|counterfeit|fraudulent token)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f97e <b>Impersonation Detector</b>\n\n"
            "Use <code>/impersonation &lt;token_address&gt;</code> to audit for:\n"
            "• Name/symbol similarity to known tokens\n"
            "• Honeypot delegates & hidden permissions\n"
            "• Related malicious contracts\n\n"
            "<i>Free during beta — spot fake tokens before you buy.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(death clock|how long (will|does)|survival|when (will|does) (it|token) die|"
            r"rug countdown|abandonment|token lifespan|longevity)\b",
            re.IGNORECASE,
        ),
        (
            "\u23f0 <b>Death Clock</b>\n\n"
            "Use <code>/deathclock &lt;chain&gt; &lt;token_address&gt;</code> to predict:\n"
            "• Days until likely death (rug or abandonment)\n"
            "• Key risk factors\n"
            "• Survival probability\n\n"
            "<i>Free during beta — know the risk before you hold.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(new launch|just launched|fresh token|new token|recent launch|"
            "brand new|just dropped|newly created)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f525 <b>Token Launches</b>\n\n"
            "Use <code>/launches [chain]</code> to see new token launches with:\n"
            "• Auto-risk flags (low/medium/high)\n"
            "• Liquidity & holder counts\n"
            "• Direct scan buttons\n\n"
            "<i>Free during beta — discover new tokens early.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(cluster|related wallet|funding path|same owner|connected wallet|"
            r"wallet group|bundle|coordinated|sniper bot|deployer funded)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f578\ufe0f <b>Wallet Clustering</b>\n\n"
            "Use <code>/cluster &lt;wallet_address&gt;</code> to detect:\n"
            "• Related wallets by behavior/funding\n"
            "• Fund origin (CEX, mixer, other tokens)\n"
            "• Deployer-funded wallets (bundle detection)\n\n"
            "<i>Free during beta — uncover hidden wallet connections.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(hello|hi|hey|yo|sup|howdy|good morning|good evening|"
            r"gm|gn|what'?s up|welcome|are you (there|alive|up|online|working)|"
            r"you (there|alive|up|online|working)|ping|wake up|respond)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f44b <b>Yo! I'm here.</b>\n\n"
            "I'm <b>Rug Munch Bot</b> \u2014 the AI analyst from the <b>Rug Munch Intelligence</b> platform. "
            "I scan tokens for rugs, profile wallets, track whales, and "
            "answer crypto questions backed by 91K+ intel documents across 25+ chains.\n\n"
            "\U0001f6e1\ufe0f <b>Quick start:</b>\n"
            "\u2022 Paste a contract address \u2192 instant scam scan\n"
            "\u2022 <code>/scan &lt;addr&gt;</code> \u2192 deep security audit\n"
            "\u2022 <code>/wallet &lt;addr&gt;</code> \u2192 wallet forensics\n"
            "\u2022 <code>/market</code> \u2192 live market overview\n"
            "\u2022 <code>$TOKEN</code> \u2192 price lookup\n"
            "\u2022 <code>/mev</code> \u2192 MEV risk & safe swap params\n"
            "\u2022 <code>/deathclock</code> \u2192 token survival prediction\n"
            "\u2022 <code>/help</code> \u2192 full command list\n\n"
            "Or just ask me anything about crypto \u2014 scams, hacks, market moves, "
            "regulations, whatever's on your mind."
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(r"\b(thank|thanks|ty|thx|appreciate|good bot|nice|great)\b", re.IGNORECASE),
        (
            "\U0001f64c You're welcome! Happy to help keep the crypto space safe.\n\n"
            "If you find this useful, tell your friends! \n"
            "Use <code>/refer</code> to get your referral link."
        ),
    ),
    (
        re.compile(
            r"\b(who made you|who created you|who built you|who developed you|"
            r"who runs you|who is your creator|who is behind you|who are you)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f916 <b>I'm Rug Munch Bot</b>\n\n"
            "Built by <b>Rug Munch Intelligence</b> \u2014 the crypto security "
            "& on-chain intel platform. I scan tokens, profile wallets, track "
            "whales, and answer crypto questions backed by 91K+ intel docs "
            "across 25+ chains."
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(help|can you|capabilities|features|commands|what do|what are|"
            r"who are you|tell me about you|about you|about this bot|"
            r"introduce yourself|describe yourself|what can you do|what else can you|"
            r"show me what you|guide|tutorial|docs|documentation)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f916 <b>Rug Munch Capabilities</b>\n\n"
            "I'm a multi-chain crypto intelligence bot. Here's what I can do:\n\n"
            "\U0001f50d <code>/scan &lt;addr&gt;</code> \u2014 Full token security audit\n"
            "\U0001f9db <code>/wallet &lt;addr&gt;</code> \u2014 Wallet forensics\n"
            "\U0001f4ca <code>/quick &lt;symbol&gt;</code> \u2014 Price lookup\n"
            "\U0001f4c8 <code>/chart &lt;addr&gt;</code> \u2014 Technical analysis\n"
            "\u2696\ufe0f <code>/compare &lt;a&gt; &lt;b&gt;</code> \u2014 Side-by-side compare\n"
            "\U0001f525 <code>/trending</code> \u2014 Hot tokens by chain\n"
            "\U0001f4f0 <code>/news</code> \u2014 Crypto headlines\n"
            "\U0001f393 <code>/scamschool</code> \u2014 Learn scam patterns\n"
            "\U0001f6e1\ufe0f <code>/mev</code> \u2014 MEV risk & safe swap params\n"
            "\U0001f97e <code>/impersonation</code> \u2014 Detect fake tokens\n"
            "\u23f0 <code>/deathclock</code> \u2014 Token longevity prediction\n"
            "\U0001f525 <code>/launches</code> \u2014 New token launch feed\n"
            "\U0001f578\ufe0f <code>/cluster</code> \u2014 Wallet clustering & funding\n"
            "\U0001f451 <code>/pricing</code> \u2014 Upgrade your tier"
            f"{footer_links()}"
        ),
    ),
]


def _is_greeting_or_thanks(text: str) -> bool:
    """Check if the message is a greeting or thanks (canned reply worthy)."""
    return bool(
        re.search(
            r"\b(hello|hi|hey|yo|sup|howdy|good morning|good evening|gm|gn|"
            r"what'?s up|ping|wake up|"
            r"thank|thanks|ty|thx|appreciate|good bot|nice|great)\b",
            text.lower(),
        )
    )


def _is_capability_question(text: str) -> bool:
    """Check if the message asks about bot capabilities (canned reply worthy).

    Stays intentionally BROAD — it is the gate that routes both the command
    menu AND the self-improvement answer to the instant canned path. The
    distinction between "what can you do" (→ menu) and "what would help you"
    (→ self-improvement wishlist) is made by :func:`_is_self_improvement_question`
    + rule order inside :func:`local_ai_response`, not by this gate.
    """
    return bool(
        re.search(
            r"\b(help|can you|capabilities|features|commands|what do|what are|"
            r"who are you|who created you|who made you|who built you|who "
            r"developed you|who runs you|who is your creator|who is behind you|"
            r"tell me about you|about you|about this bot|"
            r"introduce yourself|describe yourself|what can you do|what else can you|"
            r"show me what you|guide|tutorial|docs|documentation)\b",
            text.lower(),
        )
    )


def _is_self_improvement_question(text: str) -> bool:
    """True when the user asks what the BOT needs / could add to do better.

    Distinct from :func:`_is_capability_question`: "what can you do" is a
    menu question, but "what would help you / what capabilities could I add /
    as your developer" is a self-improvement question answered with the
    specific wishlist instead of the command menu.
    """
    return bool(_SELF_IMPROVEMENT_RE.search(text))


def _is_deep_question(text: str) -> bool:
    """Detect analysis/report requests that deserve a longer, deeper answer.

    Short queries get a snappy 1-2s answer; deep ones get up to 1200 tokens
    and a structured-analysis system prompt.
    """
    low = text.lower()
    if len(low.split()) > 40:  # long multi-part question → deep
        return True
    return bool(
        re.search(
            r"\b(analyze|analysis|explain|explain how|why|how does|how do|compare|"
            r"risk|risks|detail|detailed|technical|report|investigate|research|"
            r"break down|walk me through|what happens if|is it safe|should i|"
            r"how to|step by step|deep dive|tokenomics|audit|vulnerabilit)\b",
            low,
        )
    )


def local_ai_response(text: str, user: Any) -> str | None:
    """Tier 2: Match user intent against keyword patterns."""
    text_lower = text.lower().strip()
    for pattern, response in _INTENT_RULES:
        if pattern.search(text_lower):
            if len(text_lower) < 2:
                return None
            return response
    return None


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

_THINKING_GENERAL = "\U0001f916 <b>On it...</b>\n\n\U0001f4ac <i>{question}</i>\n\n\U0001f50d Checking our intelligence database..."

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
_INVESTIGATE_INTENT_RE = re.compile(
    r"\b(investigat\w*|deep dive|forensic|trace this|dig into)\b", re.IGNORECASE
)


# Price/market queries get a market-data message.
_PRICE_INTENT_RE = re.compile(
    r"\b(price|cost|worth|value|market cap|mcap|how much|trading at)\b", re.IGNORECASE
)

# News queries get a news-feed message.
_NEWS_INTENT_RE = re.compile(
    r"\b(news|latest|headline|happened|today|breaking|update|what'?s going on)\b", re.IGNORECASE
)

# Scam/security queries get a threat-intel message.
_SCAM_INTENT_RE = re.compile(
    r"\b(scam|rug|honeypot|exploit|hack|ponzi|fraud|safe|trust)\b", re.IGNORECASE
)


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


_STILL_DIGGING = "\U0001f916 <b>Still digging...</b>\n\n\u23f3 Cross-referencing exploit databases + checking live news..."
