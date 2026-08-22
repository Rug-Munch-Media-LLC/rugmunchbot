"""chat_ai markup constants — chunk/caps limits + beautifier tables.

Extracted verbatim from the former single-module ``chat_ai/_markup.py``.
"""

from __future__ import annotations

import re

# Telegram hard limit is 4096; leave headroom for escape expansion.
CHUNK_MAX_CHARS = 3800

# Answer length cap applied in ``_send_answer`` BEFORE ``split_in_parts``:
# a guardrail against true 1000-word walls, NOT a truncation policy. 14000
# chars ≈ 2000-2200 words ≈ 3-4 Telegram parts (each part ≤ 3800 chars) —
# room for a complete deep analysis (security + holders + deployer +
# liquidity + narrative + verdict) delivered as multiple messages. Answers
# that exceed Telegram's 4096 message limit are split across parts by
# ``split_in_parts``; only answers beyond the cap are trimmed at a
# paragraph/sentence boundary with an ellipsis so truncation is honest.
MAX_ANSWER_CHARS = 14000

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
