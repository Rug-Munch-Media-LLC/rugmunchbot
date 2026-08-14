"""RugMunchBot — rotating footer CTA (engagement polish, pure helpers).

A tiny, non-spammy "what next" line appended to answers so every reply leaves
the user one tap away from a valuable action. Design rules:

  * **Never more than one line** — a single ``⚡ Scan any token: /scan``-style
    footer, nothing more.
  * **Deterministic rotation** — the same (user, day) always yields the same
    CTA, so it never flickers mid-conversation and never spams the same line
    all week. Rotation is a simple ``crc32`` index into a static pool.
  * **HTML-safe** — the pool is static, curated text (emoji + plain command);
    no user input, no raw HTML tags, safe to send with ``ParseMode.HTML``.

This module is deliberately free of Telegram / DB / network dependencies so
any handler (or the chat_ai pipeline) can use it without import cycles.

[REVIEW] Integration point for chat answers: the AI chat pipeline renders its
final answer in ``handlers/chat_ai/__init__.py::_send_answer``. The clean
additive hook is to append ``append_footer_cta(html[-1], seed=user_id)`` to
the last chunk there. This module only ships the helper — wiring it into
chat_ai is left to that pipeline's owner to avoid a parallel-batch conflict.
"""

from __future__ import annotations

import os
import zlib
from datetime import date

from rugmunchbot.config import CHANNELS, TELEGRAM_CHANNEL

#: (emoji, short phrase, command) — one line each, curated, HTML-safe.
FOOTER_CTA_POOL: tuple[tuple[str, str, str], ...] = (
    ("\u26a1", "Scan any token", "/scan"),
    ("\U0001f4c8", "Trending now", "/trending"),
    ("\U0001f50d", "Alpha & whale flows", "/whales"),
    ("\U0001f4f0", "Latest crypto news", "/news"),
    ("\U0001f321\ufe0f", "Market pulse", "/market"),
    ("\U0001f4ac", "Ask me anything", "/chat"),
)


def footer_cta_index(seed: str) -> int:
    """Deterministic 0..len(FOOTER_CTA_POOL) index from any string seed.

    Uses ``zlib.crc32`` — not a security hash, just a stable UI rotation
    (bandit S324 does not flag crc32; it is not used for secrets).
    """
    if not seed:
        return 0
    return zlib.crc32(seed.encode("utf-8")) % len(FOOTER_CTA_POOL)


def footer_cta_for(seed: str | int = "") -> str:
    """One-line rotating CTA for a seed (stable for the same seed)."""
    emoji, phrase, command = FOOTER_CTA_POOL[footer_cta_index(str(seed))]
    return f"{emoji} {phrase}: {command}"


def rotating_cta_for(user_id: int | None = None) -> str:
    """Date-scoped rotation: same user sees one CTA per day, a new one tomorrow.

    ``user_id`` is optional (a ``None`` seed still rotates daily, shared by
    anonymous callers) so the helper is safe from any call site.
    """
    key = f"{user_id if user_id is not None else 'anon'}:{date.today().isoformat()}"
    return footer_cta_for(key)


def append_footer_cta(text: str, seed: str | int = "") -> str:
    """Append the one-line rotating CTA unless it is already present.

    Idempotent: calling twice never double-appends, so handlers that run on
    edited messages stay clean.
    """
    cta = footer_cta_for(seed)
    if cta in text:
        return text
    return f"{text}\n{cta}"


# ══════════════════════════════════════════════
# "📢 Our Channels" shill (env-driven handles)
# ══════════════════════════════════════════════
#: User-facing one-liners for the channel shill, keyed by channel role.
#: Membership comes from ``config.CHANNELS`` (env ``BOT_CHANNELS``) so the
#: shill renders the operator's own channels — never private registry data.
#: Deliberately kept as one short line per channel (compact, not a wall).
#:
#: TODO(port): the live module renders from the PRIVATE channel registry
#: ``app.core.channels_registry.CHANNEL_ROLES`` (secret-store paths + public
#: handles + numeric fallback chat ids — never ships, checklist §3/§6). OSS
#: renders the env-driven ``BOT_CHANNELS`` map instead, mirroring the ported
#: ``handlers/_help_text.py::_channel_url`` resolution order (alerts handle →
#: alpha handle env → ``t.me/c/<id>`` deep link). [REVIEW]
CHANNEL_PITCHES: dict[str, str] = {
    "news": "Crypto news 24/7 \u2014 headlines &amp; digests",
    "alerts": "Security \u2014 rug alerts &amp; exploits",
    "alpha": "Whale &amp; alpha signals \u2014 early data",
    "scans": "Scanner \u2014 suspicious tokens",
    "main": "Curated main channel \u2014 critical broadcasts",
    "updates": "Changelog \u2014 product &amp; feature news",
}


def _channel_url(key: str, chat_id: object) -> str | None:
    """Resolve one ``config.CHANNELS`` entry to a Telegram link.

    Mirrors the ported ``handlers/_help_text.py::_channel_url``: ``alerts``
    uses the ``BOT_TELEGRAM_CHANNEL`` env handle; ``alpha`` uses the
    ``BOT_TELEGRAM_ALPHA`` env handle when the operator sets one; remaining
    channels fall back to the ``https://t.me/c/<id>`` deep link derived from
    their numeric chat id (the ``-100`` supergroup prefix is stripped).
    Returns ``None`` when no link can be resolved — unresolvable channels
    are simply omitted from the rendered block.
    """
    if key == "alerts" and TELEGRAM_CHANNEL:
        return TELEGRAM_CHANNEL
    if key == "alpha":
        alpha_handle = os.getenv("BOT_TELEGRAM_ALPHA", "").strip()
        if alpha_handle:
            return alpha_handle
    numeric = str(chat_id or "").strip()
    if numeric.startswith("-100") and numeric[4:].isdigit():
        return f"https://t.me/c/{numeric[4:]}"
    return None


def channels_menu_text() -> str:
    """Compact "📢 Our Channels" block for /start, /help and onboarding.

    Renders every ``config.CHANNELS`` role that resolves to a link as one
    line: ``• <a href="...">Label</a> — one-line pitch``. Pure + deterministic
    (no network, no DB, no Telegram API) and HTML-safe for ``ParseMode.HTML``:
    balanced anchors, no raw ``<``/``>``, and no hardcoded chat ids.
    """
    lines = ["\U0001f4e2 <b>Our Channels</b>", "\u2500" * 24]
    for role, chat_id in CHANNELS.items():
        link = _channel_url(role, chat_id)
        if not link:
            continue
        pitch = CHANNEL_PITCHES.get(role) or ""
        lines.append(f'\u2022 <a href="{link}">{role.capitalize()}</a> \u2014 {pitch}')
    return "\n".join(lines)


__all__ = [
    "CHANNEL_PITCHES",
    "FOOTER_CTA_POOL",
    "append_footer_cta",
    "channels_menu_text",
    "footer_cta_for",
    "footer_cta_index",
    "rotating_cta_for",
]
