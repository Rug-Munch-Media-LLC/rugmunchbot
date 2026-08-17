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
final answer in ``handlers/chat_ai/_delivery.py::_send_answer`` (the package
``__init__`` is a re-export shim; the module path moved during the 2026-08-16
split). The clean additive hook is to append ``append_footer_cta(html[-1],
seed=user_id)`` to the last chunk there. This module only ships the helper —
wiring it into chat_ai is left to that pipeline's owner to avoid a
parallel-batch conflict. NOT yet wired: the answer already carries a
grounding footer ("⚡ Grounded in N sources … | Rug Munch Bot"), and a
second footer line on every AI answer risks reading as spam — revisit only
with a one-line CTA that replaces, never stacks, the grounding footer.
"""

from __future__ import annotations

import zlib
from datetime import date

from app.core.channels_registry import CHANNEL_ROLES

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
# "📢 Our Channels" shill (public handles only)
# ══════════════════════════════════════════════
#: User-facing one-liners for the channel shill, keyed by registry role.
#: Handles + membership come from ``channels_registry.CHANNEL_ROLES`` (the
#: canonical source) so the shill can never drift from the real channels —
#: and it renders PUBLIC HANDLES only: no numeric chat ids, no gopass paths.
#: Deliberately kept as one short line per channel (compact, not a wall).
CHANNEL_PITCHES: dict[str, str] = {
    "news": "Crypto news 24/7 \u2014 headlines &amp; digests",
    "alerts": "Security \u2014 rug alerts &amp; exploits",
    "alpha": "Whale &amp; alpha signals \u2014 early data",
    "scans": "Scanner \u2014 suspicious tokens",
    "main": "Curated main channel \u2014 critical broadcasts",
    "updates": "Changelog \u2014 product &amp; feature news",
}


def channels_menu_text() -> str:
    """Compact "📢 Our Channels" block for /start, /help and onboarding.

    Renders every registry channel with a public handle as one line:
    ``• <a href="https://t.me/<handle>">@handle</a> — one-line pitch``.
    Pure + deterministic (no network, no DB, no Telegram API) and HTML-safe
    for ``ParseMode.HTML``: balanced anchors, no raw ``<``/``>``, and the
    ``@`` in the anchor text is a literal handle — never a chat id.
    """
    lines = ["\U0001f4e2 <b>Our Channels</b>", "\u2500" * 24]
    for definition in CHANNEL_ROLES.values():
        handle = definition.public_handle
        if not handle:
            continue
        link = f"https://t.me/{handle.lstrip('@')}"
        pitch = CHANNEL_PITCHES.get(definition.role) or definition.description or ""
        lines.append(f'\u2022 <a href="{link}">{handle}</a> \u2014 {pitch}')
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
