"""Per-post-type graphics policy — the configurable post_type → image map.

Operator directive (2026-10-17): every post type carries a DISTINCT brand
graphic, and the mapping must be CONFIGURABLE per post type so the operator
can drop new assets into ``/srv/assets/brand/`` and point a post type at
them without a code change.

Design:

* :data:`POST_TYPE_GRAPHICS` is the single source of truth — a plain dict
  ``post_type -> image URL``. Edit it (or override via env) to re-point a
  post type at a new asset.
* Assets live in ``/srv/assets/brand/`` and are served from the site's
  ``/brand/`` path (``https://rugmunch.io/brand/<file>``). The operator
  authors new graphics there; adding one is a one-line dict edit.
* :func:`graphic_for` resolves a post type to its image URL, falling back
  to the brand default so a post type is never left without a graphic.
* :func:`register_graphic` lets a caller (or a future config file) add or
  override a mapping at runtime — the "configurable per post type" seam.

The map is intentionally data, not code: a JSON/env override can replace it
without touching this module. Fail-open everywhere — an unknown post type
returns the brand default, never "".
"""

from __future__ import annotations

import os
from typing import Final

#: Public base for brand assets (served by the site from /srv/assets/brand/).
BRAND_BASE_URL: Final = os.getenv("RMI_BRAND_BASE_URL", "https://rugmunch.io/brand")

#: The brand default — the clean square app logo. Used when a post type has
#: no explicit mapping (never leaves a post graphic-less).
DEFAULT_GRAPHIC: Final = f"{BRAND_BASE_URL}/rmi_badge_v4.png"

#: post_type → brand asset filename. The filename is joined to
#: :data:`BRAND_BASE_URL`; a full http(s) URL is used verbatim so an
#: operator can point a post type at an off-site asset.
#:
#: Assets present in /var/www/rmi/brand/ (served at /brand/, 2026-10-17):
#:   daily-rug-logo.jpeg · rug-munch-logo.jpg · rug-munch-media-llc.png
#:   rmi-breaking-news.jpeg · rmi-midday-market-report.jpeg
#:   rmi-evening-market-wrap.jpeg · rmi-scam-alert.jpeg · rmi-whale-alert.jpeg
#:   rmi_badge_v4.png · rmi_badge_v5.png
#:
#: IDENTITY CHROME IS NEVER A POST GRAPHIC: ``avatar.jpg``,
#: ``current_cryptorugmuncher.png``, the banners and ``1500x500.jpeg`` are
#: profile imagery. They were the retired fallback defect; the risk/alpha
#: family now uses the dedicated alert/market originals instead.
POST_TYPE_GRAPHICS: Final[dict[str, str]] = {
    # ── flagship longform ──────────────────────────────────────────────
    "daily_rug": "daily-rug-logo.jpeg",
    "daily_rug_update": "daily-rug-logo.jpeg",
    "market_summary": "rmi-evening-market-wrap.jpeg",
    "market": "rmi-evening-market-wrap.jpeg",
    # ── the three daily reports (2026-10-17) ───────────────────────────
    # Morning Brief → daily_rug; Midday Market Report → midday_report;
    # Evening Wrap → evening_wrap. Operator originals in /var/www/rmi/brand/
    # (served at /brand/).
    "midday_report": "rmi-midday-market-report.jpeg",
    "evening_wrap": "rmi-evening-market-wrap.jpeg",
    # ── news / editorial ───────────────────────────────────────────────
    "news": "rmi-breaking-news.jpeg",
    "breaking": "rmi-breaking-news.jpeg",
    "feature": "rug-munch-logo.jpg",
    "hybrid": "rug-munch-logo.jpg",
    "themed": "rug-munch-logo.jpg",
    "roundup": "rug-munch-logo.jpg",
    "digest": "rug-munch-logo.jpg",
    # ── risk / alerts ──────────────────────────────────────────────────
    # Never a profile photo (see IDENTITY CHROME note above).
    "scan": "rmi_badge_v5.png",
    "scanner_alert": "rmi_badge_v5.png",
    "alert": "rmi-breaking-news.jpeg",
    "scam_alert": "rmi-scam-alert.jpeg",
    "exploit": "rmi-breaking-news.jpeg",
    "shill": "rmi-scam-alert.jpeg",
    "shill_watch": "rmi-scam-alert.jpeg",
    "whale": "rmi-whale-alert.jpeg",
    "alpha": "rmi-midday-market-report.jpeg",
    "dislocation": "rmi-midday-market-report.jpeg",
    "lp_expiry": "rmi-scam-alert.jpeg",
    "social_spike": "rmi-whale-alert.jpeg",
    # ── product / system ───────────────────────────────────────────────
    "update": "rug-munch-media-llc.png",
    "system": "rug-munch-media-llc.png",
}

#: Env override: ``RMI_POST_GRAPHICS="scan=foo.png,news=bar.jpg"`` merges on
#: top of the defaults so an operator can re-point a post type without a
#: code change (the "configurable per post type" contract).
_ENV_OVERRIDE: Final = os.getenv("RMI_POST_GRAPHICS", "")


def _apply_env_override() -> None:
    """Merge ``RMI_POST_GRAPHICS`` (``type=file,type=file``) into the map."""
    if not _ENV_OVERRIDE.strip():
        return
    for pair in _ENV_OVERRIDE.split(","):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key, value = key.strip().lower(), value.strip()
        if key and value:
            POST_TYPE_GRAPHICS[key] = value


_apply_env_override()


def _to_url(asset: str) -> str:
    """Resolve an asset reference to a full URL (verbatim when already one)."""
    value = str(asset or "").strip()
    if not value:
        return DEFAULT_GRAPHIC
    if value.startswith(("http://", "https://")):
        return value
    return f"{BRAND_BASE_URL}/{value.lstrip('/')}"


def graphic_for(post_type: str) -> str:
    """The brand graphic URL for *post_type* (brand default when unmapped).

    Never returns "" — a post type always resolves to a graphic so the
    image doctrine (every post carries an image) holds. Pure — never raises.
    """
    key = str(post_type or "").strip().lower()
    asset = POST_TYPE_GRAPHICS.get(key)
    if asset is None:
        return DEFAULT_GRAPHIC
    return _to_url(asset)


def graphic_for_type(post_type: str, override: str = "") -> str:
    """Resolve a post type to its graphic, honouring a per-report override.

    The three daily reports each carry a configurable ``*_GRAPHIC`` env
    value (a filename or full URL). Historically the publish modules passed
    that value straight to :func:`graphic_for` as if it were a post type —
    a filename is not a key in :data:`POST_TYPE_GRAPHICS`, so the lookup
    always missed and every report fell back to the brand default.

    This helper fixes the contract: when *override* is a full URL it is used
    verbatim; when it is a bare filename it is joined to the brand base;
    when it is empty the *post_type* mapping (or the brand default) applies.
    """
    value = str(override or "").strip()
    if value:
        return _to_url(value)
    return graphic_for(post_type)


def register_graphic(post_type: str, asset: str) -> None:
    """Add/override a post_type → asset mapping at runtime.

    The configurable seam: a future config file (or the operator) can call
    this to wire a newly-authored graphic to a post type without editing
    the module. ``asset`` may be a filename (joined to the brand base) or a
    full URL.
    """
    key = str(post_type or "").strip().lower()
    if key and str(asset or "").strip():
        POST_TYPE_GRAPHICS[key] = str(asset).strip()


__all__ = [
    "BRAND_BASE_URL",
    "DEFAULT_GRAPHIC",
    "POST_TYPE_GRAPHICS",
    "graphic_for",
    "graphic_for_type",
    "register_graphic",
]
