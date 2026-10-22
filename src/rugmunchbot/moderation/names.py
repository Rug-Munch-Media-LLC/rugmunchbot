"""Username / display-name policy: impersonation and lookalike detection.

Folding order matters: homoglyphs are translated BEFORE the ASCII strip,
otherwise an "admin" written with a Cyrillic first letter would lose that
letter and the lookalike would slip through.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rugmunchbot.moderation.models import MsgContext

BRAND = "rugmunch"
_CLOSE = 0.80
# Cyrillic / Greek / digit homoglyphs, written as escapes so the source stays
# pure ASCII (and the linter does not flag every entry as ambiguous).
_CONFUSABLE = str.maketrans(
    {
        "\u0430": "a",
        "\u0432": "b",
        "\u0435": "e",
        "\u043a": "k",
        "\u043c": "m",
        "\u043d": "h",
        "\u043e": "o",
        "\u0440": "p",
        "\u0441": "c",
        "\u0442": "t",
        "\u0443": "y",
        "\u0445": "x",
        "\u0456": "i",
        "\u0458": "j",
        "\u0455": "s",
        "\u0501": "d",
        "\u0261": "g",
        "\u03b1": "a",
        "\u03b2": "b",
        "\u03b5": "e",
        "\u03b9": "i",
        "\u03ba": "k",
        "\u03bf": "o",
        "\u03c1": "p",
        "\u03c4": "t",
        "\u03c5": "u",
        "\u03c7": "x",
        "0": "o",
        "1": "l",
        "3": "e",
        "5": "s",
        "$": "s",
        "@": "a",
    }
)
_GENERIC = {"chat", "group", "channel", "the", "and", "rug", "munch"}


def fold(text: str) -> str:
    """Lowercase, map homoglyphs, ASCII-fold, collapse repeats and spaces."""
    lowered = (text or "").lower().translate(_CONFUSABLE)
    ascii_only = unicodedata.normalize("NFKD", lowered).encode("ascii", "ignore").decode()
    collapsed = re.sub(r"(.)\1+", r"\1", ascii_only)
    return re.sub(r"[^a-z0-9]+", " ", collapsed).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in fold(text).split() if t}


def _close_to(token: str, target: str) -> bool:
    if token == target:
        return True
    if len(token) < 5 or len(target) < 5:
        return False
    return difflib.SequenceMatcher(None, token, target).ratio() >= _CLOSE


def _brand_like(tokens: set[str]) -> bool:
    return any(_close_to(t, BRAND) for t in tokens)


def _role_like(tokens: set[str], roles: tuple[str, ...]) -> bool:
    for role in (fold(r) for r in roles if r):
        if not role:
            continue
        for token in tokens:
            if token == role:
                return True
            # Prefix match only for roles long enough to stay unambiguous:
            # "administrator" matches "admin", but "devon" never matches "dev".
            if len(role) >= 5 and token.startswith(role):
                return True
    return False


def _title_like(tokens: set[str], chat_title: str) -> bool:
    title = {t for t in _tokens(chat_title) if len(t) >= 5 and t not in _GENERIC}
    return any(_close_to(token, other) for token in tokens for other in title)


def impersonation_reason(ctx: MsgContext, policy) -> str | None:
    """Return why *ctx* looks like an impostor, or None when the name is fine.

    Defaults stay conservative: a bare role word ("dev", "admin") is only
    flagged when ``impersonation_strict`` is on, because plenty of real members
    are named "dev". Brand+role is always flagged.
    """
    if ctx.is_admin:
        return None
    name_tokens = _tokens(f"{ctx.display_name} {ctx.username}")
    if not name_tokens:
        return None
    roles = policy.impersonation_roles or ()
    brand = _brand_like(name_tokens)
    role = _role_like(name_tokens, roles)
    title = _title_like(name_tokens, ctx.chat_title)
    strict = bool(policy.impersonation_strict)
    if brand and role:
        return "brand + role in name"
    if role and strict:
        return "role name (strict)"
    if title and (role or strict):
        return "chat-name lookalike"
    if brand and strict:
        return "brand lookalike (strict)"
    return None


__all__ = ["BRAND", "fold", "impersonation_reason"]
