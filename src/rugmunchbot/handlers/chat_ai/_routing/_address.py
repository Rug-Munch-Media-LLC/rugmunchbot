"""Bare-address auto-detect — paste a contract, get the scan card.

A message that is *only* a contract address (optionally followed by one
short label such as a ticker or chain hint) triggers the scan card with no
command, matching the competitor's paste-to-scan behaviour. Everything else
must fall through to the AI pipeline — hijacking normal chat is far worse
than asking for one extra tap, so the rules are deliberately strict:

  * exactly ONE address-shaped token in the message (zero or several → not
    ours; several addresses are ambiguous and go to the AI);
  * at most one extra token, ≤ 24 chars, label charset only (letters,
    digits, ``$ _ - .``) — ``"is this 0x… a rug?"`` has four leftover tokens
    and never matches;
  * ``@bot`` mentions are stripped first (a group mention is addressing,
    not a label);
  * EVM addresses may arrive without ``0x`` (Telegram clients and copiers
    drop it often) and are canonicalised to ``0x…``.

Group etiquette is enforced upstream by
``handlers.chat_ai._routing._handler._is_bot_addressed`` — this module only
answers "is the message an address?".

One public entry point: :func:`address_only`.
"""

from __future__ import annotations

import re

from rugmunchbot.formatters import is_evm, is_sol

#: Label token: ticker / chain hint / short name. No spaces, no punctuation
#: beyond ``$ _ - .``.
LABEL_RE = re.compile(r"^[A-Za-z0-9$_][A-Za-z0-9$_\-.]{0,23}$")

#: 40 hex chars with the ``0x`` stripped by a client copy.
HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")

#: Edge punctuation that survives a copy/paste (``(addr)``, ``addr,``).
STRIP_CHARS = "()[]{}<>,.;:!?\"'`"

MAX_LABEL_TOKENS = 1


def _clean(token: str) -> str:
    """Strip surrounding brackets / punctuation from one whitespace token."""
    return token.strip(STRIP_CHARS)


def _canonical(token: str) -> str | None:
    """Address for a token, or ``None`` when it is not an address."""
    candidate = _clean(token)
    if not candidate:
        return None
    if HEX40_RE.match(candidate):
        candidate = f"0x{candidate}"
    if is_evm(candidate) or is_sol(candidate):
        return candidate
    return None


def address_only(text: str) -> str | None:
    """Return the lone contract address in ``text``, else ``None``.

    ``None`` means "not ours" — the caller must continue to the AI pipeline.
    """
    tokens = [tok for tok in text.split() if tok and not tok.startswith("@")]
    if not tokens:
        return None
    addresses = [(i, addr) for i, tok in enumerate(tokens) if (addr := _canonical(tok))]
    if len(addresses) != 1:
        return None
    index, address = addresses[0]
    labels = [tok for i, tok in enumerate(tokens) if i != index]
    if len(labels) > MAX_LABEL_TOKENS:
        return None
    if labels and not LABEL_RE.match(_clean(labels[0])):
        return None
    return address
