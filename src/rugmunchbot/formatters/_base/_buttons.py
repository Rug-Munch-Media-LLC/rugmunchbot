"""Trade-button rows — per-chain DEX deep links with ref attribution."""

from __future__ import annotations

from telegram import InlineKeyboardButton

from rugmunchbot.config import (
    CHAIN_IDS,
    DEX_REF_LINKS,
)
from rugmunchbot.formatters._base._links import (
    DEX_BUTTON_PRIORITY,
    DEX_BUTTONS_PER_ROW,
    UNKNOWN_CHAIN_MINIMAL,
    dexscreener_url,
    dextools_url,
)


def _ref_platform_buttons(
    token: str, chain: str, only: tuple[str, ...] | None = None
) -> list[InlineKeyboardButton]:
    """Chain-filtered ``DEX_REF_LINKS`` buttons in ``DEX_BUTTON_PRIORITY`` order.

    Fills ``{token}`` / ``{chain}`` / ``{chain_id}`` placeholders from the
    scan's address + chain so every link lands on the token with our ref
    embedded. ``only`` restricts to a fixed platform subset (used for the
    unknown-chain minimal set); ``None`` filters by ``dex["chains"]``.
    """
    chain_l = (chain or "").lower()
    ordered = sorted(
        DEX_REF_LINKS.items(),
        key=lambda kv: (
            DEX_BUTTON_PRIORITY.index(kv[0])
            if kv[0] in DEX_BUTTON_PRIORITY
            else len(DEX_BUTTON_PRIORITY)
        ),
    )
    buttons: list[InlineKeyboardButton] = []
    for key, dex in ordered:
        if only is not None:
            if key not in only:
                continue
        elif chain_l not in dex.get("chains", []):
            continue
        url = dex["url"].format(
            token=token,
            chain=chain_l,
            chain_id=CHAIN_IDS.get(chain_l, 1),
        )
        buttons.append(InlineKeyboardButton(f"{dex['emoji']} {dex['name']}", url=url))
    return buttons


def dex_button_rows(
    token: str, chain: str, per_row: int = DEX_BUTTONS_PER_ROW
) -> list[list[InlineKeyboardButton]]:
    """Chain-filtered referral trade buttons grouped into rows of ≤ ``per_row``.

    Renders the full ``DEX_REF_LINKS`` set for the chain in
    ``DEX_BUTTON_PRIORITY`` order (GMGN/Jupiter lead), with DexScreener +
    DexTools chart buttons appended after the referral platforms. Every
    token-embedding platform (GMGN/Photon/Birdeye charts, Jupiter/1inch/
    PancakeSwap swaps) gets the scan's address filled in — see
    ``_ref_platform_buttons``. Telegram signup bots (OdinBot/Banana Gun)
    and home-only terminals (Axiom/Padre) carry no per-token URL by design
    (documented in ``config.DEX_REF_LINKS``). An
    empty/unknown chain returns a safe minimal set — the Telegram trading
    bots (OdinBot / Banana Gun) whose ref-coded start links never depend on
    the token address — so no token-page URL is ever built for the wrong
    chain. Pure: no network, no DB.
    """
    chain_l = (chain or "").lower()
    if chain_l in ("", "unknown"):
        buttons = _ref_platform_buttons(token, chain_l, only=UNKNOWN_CHAIN_MINIMAL)
    else:
        buttons = _ref_platform_buttons(token, chain_l)
        # Chart trade links — DexScreener (no ref program) + DexTools (ref via
        # REF_CODES / RMI_REF_DEXTOOLS when configured). GMGN already ships via
        # DEX_REF_LINKS above, so it is not duplicated here.
        buttons.append(
            InlineKeyboardButton("\U0001f986 DexScreener", url=dexscreener_url(token, chain_l))
        )
        buttons.append(
            InlineKeyboardButton("\U0001f4c8 DexTools", url=dextools_url(token, chain_l))
        )
    per_row = max(1, per_row)
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def dex_buttons(token: str, chain: str) -> list[InlineKeyboardButton]:
    """Flattened referral trade buttons (back-compat) — see ``dex_button_rows``.

    Returns the same chain-filtered full set in priority order as a flat
    list so existing callers (``keyboards.scan_result_kb`` chunking 2/row)
    keep working unchanged.
    """
    return [button for row in dex_button_rows(token, chain) for button in row]
