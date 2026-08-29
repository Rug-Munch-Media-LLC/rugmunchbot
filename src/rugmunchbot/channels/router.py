"""Pure news-routing decision — every scored story goes to EXACTLY ONE destination.

The tg_news post path scores each swept article with the reranker
(``channels/importance.py``) and asks :func:`route_news` where the story
belongs. The returned destination maps to a chat id in the scheduler
(``channels/scheduler.py::_route_chat_id``):

* ``"skip"``  — importance below the news floor (default 0.4): don't post
* ``"news"``  — news-band importance (default 0.4-0.7): the news channel
* ``"main"``  — importance at/above the main gate (default 0.7): the
                operator's @cryptorugmuncher channel
* ``"alpha"`` — alpha-category story below the main gate: the alpha channel

Precedence matters: ``"main"`` always wins over ``"alpha"`` (an alpha-category
story that also clears the main gate is a main-channel story), and the alpha
category wins over the news band (alpha-category stories never land on the
news channel).

This module is intentionally **pure** — no network, no DB, no Telegram. The
thresholds are module constants read once at import from env (matching the
``channels/importance.py`` pattern); the decision function itself does no I/O.

Env knobs (read once at import, overridable):
* ``RMI_ROUTE_NEWS_MIN_IMPORTANCE`` — news-band floor (default 0.4)
* ``RMI_ROUTE_MAIN_MIN_IMPORTANCE`` — main-channel gate (default 0.7)
"""

from __future__ import annotations

import os

#: Default news-band floor — below this a story is skipped. Matches the news
#: channel's ``channels/importance.py::IMPORTANCE_THRESHOLD`` default so the
#: routed bands and the legacy gate agree.
ROUTE_NEWS_MIN_IMPORTANCE_DEFAULT: float = 0.4
#: Default main-channel gate — at/above this a story is a "bigger story" for
#: the operator's @cryptorugmuncher channel (mirrors
#: ``personal_channel.py::PERSONAL_CHANNEL_THRESHOLD`` default).
ROUTE_MAIN_MIN_IMPORTANCE_DEFAULT: float = 0.7
#: Swept category string that routes a story to the alpha channel (matches
#: the ``classify_channel_category`` "alpha" bucket in ``telegram_news``).
ROUTE_ALPHA_CATEGORY: str = "alpha"
#: Swept category string for scam-call / shill-intel channels (matches the
#: ``classify_channel_category`` "scam" bucket). Scam-call content that clears
#: the news floor goes to the ALERTS channel (@rmialerts — its doctrine admits
#: scam/rug detections), and above the main gate escalates to main. It never
#: lands on the news channel: scam calls are danger intel, not headlines.
ROUTE_SCAM_CATEGORY: str = "scam"

# W18 (2026-08-25): MAJOR-EVENT escalation categories. A story whose
# category matches these goes to MAIN (@cryptorugmuncher) regardless of its
# importance score — an active exploit or confirmed rug must never be
# buried by the importance queue. Env-extendable, comma-separated.
ROUTE_MAIN_ALWAYS_CATEGORIES: frozenset[str] = frozenset(
    c.strip()
    for c in os.getenv(
        "RMI_ROUTE_MAIN_ALWAYS",
        "exploit,hack,rug_confirmed,security_incident",
    ).split(",")
    if c.strip()
)


def _env_float(name: str, default: float) -> float:
    """Clamped 0..1 float env helper (same semantics as importance.py)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0.0, min(1.0, float(raw.strip())))
    except (TypeError, ValueError):
        return default


#: News-band floor (env ``RMI_ROUTE_NEWS_MIN_IMPORTANCE``).
ROUTE_NEWS_MIN_IMPORTANCE: float = _env_float(
    "RMI_ROUTE_NEWS_MIN_IMPORTANCE", ROUTE_NEWS_MIN_IMPORTANCE_DEFAULT
)
#: Main-channel gate (env ``RMI_ROUTE_MAIN_MIN_IMPORTANCE``).
ROUTE_MAIN_MIN_IMPORTANCE: float = _env_float(
    "RMI_ROUTE_MAIN_MIN_IMPORTANCE", ROUTE_MAIN_MIN_IMPORTANCE_DEFAULT
)


def route_news(importance: float, category: str) -> str:
    """Route one scored story to exactly one destination.

    Returns ``"skip"`` / ``"news"`` / ``"main"`` / ``"alpha"`` / ``"alerts"``.

    Bands (defaults, env-overridable):
    * importance < ``ROUTE_NEWS_MIN_IMPORTANCE`` (0.4)        → ``"skip"``
    * ``ROUTE_NEWS_MIN_IMPORTANCE`` ≤ importance
      < ``ROUTE_MAIN_MIN_IMPORTANCE`` (0.4-0.7), not alpha     → ``"news"``
    * importance ≥ ``ROUTE_MAIN_MIN_IMPORTANCE`` (0.7)         → ``"main"``
    * category == ``ROUTE_ALPHA_CATEGORY`` (alpha) and
      importance < ``ROUTE_MAIN_MIN_IMPORTANCE``               → ``"alpha"``
    * category == ``ROUTE_SCAM_CATEGORY`` (scam) and
      importance < ``ROUTE_MAIN_MIN_IMPORTANCE``               → ``"alerts"``
      (scam-call content in the news band is danger intel — it goes to
      @rmialerts, never the news channel)

    ``"main"`` always beats ``"alpha"``/``"alerts"``: an alpha- or scam-
    category story that also clears the main gate is a main-channel story.
    """
    # W18: major events escalate first — importance can't bury an active
    # exploit or confirmed rug.
    if category and category.lower() in ROUTE_MAIN_ALWAYS_CATEGORIES:
        return "main"
    if importance >= ROUTE_MAIN_MIN_IMPORTANCE:
        return "main"
    if category == ROUTE_ALPHA_CATEGORY:
        return "alpha"
    if category == ROUTE_SCAM_CATEGORY:
        return "alerts" if importance >= ROUTE_NEWS_MIN_IMPORTANCE else "skip"
    if importance >= ROUTE_NEWS_MIN_IMPORTANCE:
        return "news"
    return "skip"


# ── WO-2 (2026-08-25): severity/type → channel matrix + demotion ─────────


#: Severities that may hit @rmialerts (matrix: "Severity-gated CRITICAL/HIGH,
#: no filler"). Everything else alert-ish is skipped, never softened.
ALERT_SEVERITIES: frozenset[str] = frozenset({"critical", "high"})

#: Known item types and their native (non-main) destination. ``main`` is
#: always ADDITIVE on top of the native channel, never a replacement.
_NATIVE_DESTINATION: dict[str, str] = {
    "alert": "alerts",
    "news": "news",
    "scan": "scans",
    "shill": "shills",
    "whale": "alpha",
    "alpha": "alpha",
}


def route_item(item_type: str, severity: str, importance: float) -> str:
    """Pure severity/type → channel matrix (CONTENT-STRATEGY-CANONICAL.md).

    Order matters:

    1. ``alert`` type is severity-gated FIRST — only CRITICAL/HIGH alerts
       post (to ``alerts``); lower-severity alert noise is ``skip``ped no
       matter its importance score.
    2. importance ≥ main gate → ``main`` (additive escalation; subject to
       the ≤9/day cap enforced at send time by ``channels.main_cap``).
    3. otherwise the item's NATIVE channel per type (scans/shills/alpha).
    4. news-type items below the main gate fall through the importance
       bands (news floor → ``news``, under → ``skip``).

    Unknown types are treated as news. Pure — no I/O, never raises.
    """
    t = str(item_type or "").strip().lower()
    sev = str(severity or "").strip().lower()
    if t == "alert":
        return "alerts" if sev in ALERT_SEVERITIES else "skip"
    if importance >= ROUTE_MAIN_MIN_IMPORTANCE:
        return "main"
    native = _NATIVE_DESTINATION.get(t)
    if native is not None and t != "news":
        return native
    if importance >= ROUTE_NEWS_MIN_IMPORTANCE:
        return "news"
    return "skip"


def demote_main(item_type: str) -> str:
    """Overflow destination when the main cap blocks an escalation.

    Per spec: "overflow demotes to news/shills per type". Items whose
    native-channel copy ALREADY exists (alerts/scans/alpha/news digests)
    demote to that native lane — i.e. the additive main copy is dropped,
    nothing is lost. Returns "" only for types with no lane at all.
    Pure — never raises.
    """
    t = str(item_type or "").strip().lower()
    return _NATIVE_DESTINATION.get(t, "news")


__all__ = [
    "ALERT_SEVERITIES",
    "ROUTE_ALPHA_CATEGORY",
    "ROUTE_MAIN_MIN_IMPORTANCE",
    "ROUTE_MAIN_MIN_IMPORTANCE_DEFAULT",
    "ROUTE_NEWS_MIN_IMPORTANCE",
    "ROUTE_NEWS_MIN_IMPORTANCE_DEFAULT",
    "ROUTE_SCAM_CATEGORY",
    "demote_main",
    "route_item",
    "route_news",
]
