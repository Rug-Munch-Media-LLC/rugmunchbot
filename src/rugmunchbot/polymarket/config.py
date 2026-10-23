"""Engine settings, read from the environment only.

Two credentials are optional and NEVER invented:

``POLYMARKET_REFERRAL_URL``
    Only published when set and when it is a genuine polymarket.com URL.
``POLYMARKET_BUILDER_CODE``
    bytes32 builder code from the Polymarket Builders profile. Rendered as a
    disclosure when well-formed; ignored (with a log line) otherwise.

The engine is read-only: it never trades, signs, or posts without an explicit
publisher callback and ``POLYMARKET_DRY_RUN=false``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.polymarket.config")

OUR_WALLET = "0x1E3AC01d0fdb976179790BDD02823196A92705C9"
_BUILDER_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_DEFAULT_CATEGORIES = ("crypto", "macro", "politics", "tech")


def _read_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _read_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("polymarket: %s is not a number (%r); using %s", name, raw, default)
        return default


def _read_referral_url(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    url = raw.strip()
    host = (urlparse(url).hostname or "").lower()
    if not url.startswith("https://") or not (
        host == "polymarket.com" or host.endswith(".polymarket.com")
    ):
        logger.warning(
            "polymarket: POLYMARKET_REFERRAL_URL rejected (not an https polymarket.com URL)"
        )
        return None
    return url


def _read_builder_code(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    code = raw.strip()
    if not _BUILDER_RE.match(code):
        logger.warning("polymarket: POLYMARKET_BUILDER_CODE rejected (expected bytes32 hex)")
        return None
    return code


@dataclass(frozen=True)
class PolySettings:
    """Runtime knobs. Defaults are conservative and dry-run."""

    our_wallet: str = OUR_WALLET
    referral_url: str | None = None
    builder_code: str | None = None
    dry_run: bool = True
    min_volume_usd: float = 250_000.0
    min_liquidity_usd: float = 50_000.0
    min_abs_change_1d: float = 0.05
    min_trade_notional_usd: float = 10_000.0
    low_history_max_markets: int = 5
    max_markets: int = 3
    max_flow_trades: int = 5
    categories: tuple[str, ...] = field(default_factory=lambda: _DEFAULT_CATEGORIES)
    min_post_interval_s: int = 900

    @classmethod
    def from_env(cls) -> PolySettings:
        cats = os.getenv("POLYMARKET_CATEGORIES", "").strip()
        return cls(
            our_wallet=os.getenv("POLYMARKET_WALLET", OUR_WALLET).strip() or OUR_WALLET,
            referral_url=_read_referral_url(os.getenv("POLYMARKET_REFERRAL_URL")),
            builder_code=_read_builder_code(os.getenv("POLYMARKET_BUILDER_CODE")),
            dry_run=_read_bool("POLYMARKET_DRY_RUN", True),
            min_volume_usd=_read_float("POLYMARKET_MIN_VOLUME_USD", 250_000.0),
            min_liquidity_usd=_read_float("POLYMARKET_MIN_LIQUIDITY_USD", 50_000.0),
            min_abs_change_1d=_read_float("POLYMARKET_MIN_ABS_CHANGE_1D", 0.05),
            min_trade_notional_usd=_read_float("POLYMARKET_MIN_TRADE_NOTIONAL_USD", 10_000.0),
            max_markets=int(_read_float("POLYMARKET_MAX_MARKETS", 3)),
            categories=tuple(c.strip().lower() for c in cats.split(",") if c.strip())
            or _DEFAULT_CATEGORIES,
        )
