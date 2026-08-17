"""Per-channel HTML content formatters for the bot-owned Telegram channels.

One formatter per channel:
* ``format_news_post``   — news channel (curated headlines, source-cited).
* ``format_breaking_post`` — news channel (solo 🚨 BREAKING story + official link).
* ``format_feature_post`` — news channel (solo ⭐ top-story card: image +
  one-line hook + longer lede — used for single/high-importance stories).
* ``format_hybrid_post`` — news channel ("top 3 + one image" — lead story
  card with hook + a short numbered list of the rest).
* ``format_themed_post`` — news channel (🧵 roundup of stories sharing a
  topic/token, e.g. ``$BTC``).
* ``format_alerts_post`` — alerts channel (urgent risk digest: why-flagged
                           first, /scan links).
* ``format_scan_card``  — scans channel (@rmiscans) compact per-token card
                           (verdict + risk score + token + chain + scan link).
* ``format_alpha_post``  — alpha channel (high-signal: whale moves + smart
                           money + launches).

Voice contract: degen-fluent and risk-first like the bot persona, but
channel-appropriate — news stays neutral-curated, alpha is high-signal, alerts
read like an urgent warning. Every story carries its canonical source link and
(where available) image. All backend/user-supplied text is HTML-escaped
(``quote=False`` so Telegram ``<a>``/``<code>`` tags we build ourselves stay
intact); summary hooks are flattened of raw HTML/markdown before display so no
unrendered markup leaks into a post. Messages stay under Telegram's 4096-char
soft limit and always carry the bot footer.
"""

from __future__ import annotations

import html
import re
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from app.domains.telegram.rugmunchbot.formatters import footer_links, gmgn_url, sep, short_addr

_MAX_BODY_CHARS = 4090
#: Length of the summary shown under a solo breaking post.
_BREAKING_SUMMARY_CHARS = 600
#: Length of the lede under a solo feature-card post (after the one-line hook).
_FEATURE_SUMMARY_CHARS = 420
#: Length of the one-line hook under a feature/hybrid lead story.
_FEATURE_HOOK_CHARS = 160
#: One-line plain-text hook caps: digest stories stay a single line, alpha
#: items keep the same tight budget, breaking posts keep more room.
_DIGEST_TAKEAWAY_CHARS = 150
_ALPHA_TAKEAWAY_CHARS = 120
#: One-line AI "why it matters" commentary cap (the LLM already emits ≤180
#: chars via ``_digest.generate_commentary``; this is a defensive re-clip).
_COMMENTARY_HOOK_CHARS = 180

_WHITESPACE_RE = re.compile(r"\s+")
_MARKUP_TAGS_RE = re.compile(r"<[^>]*>")
_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKDOWN_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MARKDOWN_EMPH_RE = re.compile(r"(\*\*|__|~~|`{1,3}|>|#+\s?)")


class NewsPost(str):
    """A formatted post: a plain ``str`` plus an optional attached image URL.

    ``image_url`` is ``""`` when the post has no usable image. Because the
    value IS a ``str``, existing callers (``_safe_send``,
    ``post_news_update``, tests) keep working unchanged; photo-aware callers
    read ``.image_url`` and route through ``channels._media.send_news_photo``.
    """

    image_url: str = ""


def _pick_image_url(value: object) -> str:
    """Return *value* when it is a usable http(s) image URL, else ``""``."""
    if isinstance(value, str) and value.strip():
        url = value.strip()
        if url.startswith(("http://", "https://")):
            return url
    return ""


def _esc(value: object) -> str:
    """HTML-escape text for Telegram parse_mode=HTML (quotes left intact)."""
    return html.escape(str(value), quote=False)


def _is_placeholder(value: object) -> bool:
    """True when a name/symbol field carries no real identity (fail-open)."""
    return str(value or "").strip().lower() in ("", "unknown", "???", "n/a", "null", "none")


def _clip(text: str, limit: int = _MAX_BODY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _strip_markup(text: object) -> str:
    """Flatten raw HTML/markdown from a source summary into plain text (fail-open).

    Unescapes HTML entities first (so entity-encoded tags are stripped too),
    then removes markdown link/image syntax, HTML tags, list bullets and
    emphasis markers, and collapses whitespace. Returns ``""`` when nothing
    usable remains — never raises, so a hostile summary can never inject raw
    HTML into a post.
    """
    value = str(text or "")
    if not value.strip():
        return ""
    value = html.unescape(value)
    value = _MARKDOWN_LINK_RE.sub(r"\1", value)
    value = _MARKUP_TAGS_RE.sub(" ", value)
    value = _MARKDOWN_BULLET_RE.sub("", value)
    value = _MARKDOWN_EMPH_RE.sub("", value)
    value = _WHITESPACE_RE.sub(" ", value)
    return value.strip()


def _takeaway(text: object, limit: int = _DIGEST_TAKEAWAY_CHARS) -> str:
    """One-line plain-text hook from a summary (HTML-safe, clipped, fail-open)."""
    hook = _strip_markup(text)
    if not hook:
        return ""
    return _clip(hook, limit)


def _first_sentence(text: str) -> str:
    """Return the first sentence of *text* as a one-line hook (≤160 chars).

    The hook is the compelling one-liner that sits directly under a solo /
    hybrid lead headline (requirement: "rewritten title + a one-line hook",
    not a bare link). Feed it already-flattened plain text (``_strip_markup``);
    callers escape the result once at insertion time. Empty input → ``""``.
    Never raises.
    """
    text = (text or "").strip()
    if not text:
        return ""
    for delim in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        idx = text.find(delim)
        if idx != -1:
            candidate = text[: idx + 1].strip()
            if candidate:
                text = candidate
            break
    return text[:_FEATURE_HOOK_CHARS].rstrip()


def _lede_after_hook(summary: str, hook: str) -> str:
    """The rest of *summary* after *hook*, clipped to the feature-lede limit."""
    rest = summary
    if hook:
        rest = rest[len(hook) :].lstrip(" .")
    return _clip(rest, _FEATURE_SUMMARY_CHARS)


def _risk_emoji(risk_level: str) -> str:
    return {"critical": "🔴", "high": "🔴", "medium": "🟡", "low": "🟢", "safe": "✅"}.get(
        str(risk_level).lower(), "⚪"
    )


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%b %d, %H:%M UTC")


# ── News channel ──────────────────────────────────────────────────────────


def _story_line(item: dict[str, Any], index: int) -> list[str]:
    """One numbered story: headline link + one-line takeaway + AI why-it-matters + source.

    Shared by the digest (``format_news_post``) and the themed roundup
    (``format_themed_post``) so every story gets the same curated treatment:
    a headline linking to the canonical source URL, then a clipped plain-text
    hook from the summary with the source name — never a bare link, never a
    raw HTML/markdown leak. When the scheduler attached an AI commentary
    (``item["commentary"]``, a "why it matters" one-liner) it renders as a
    💡 line so every posted story carries context beyond the headline.
    """
    title = _esc(item.get("title") or "Untitled")
    url = (item.get("url") or "").strip()
    if url:
        title = f'<a href="{html.escape(url, quote=True)}">{title}</a>'
    source = _esc(item.get("source") or "unknown")
    lines = [f"{index}. {title}"]
    takeaway = _esc(_takeaway(item.get("summary") or item.get("description")))
    commentary = _esc(_takeaway(item.get("commentary"), _COMMENTARY_HOOK_CHARS))
    if takeaway:
        lines.append(f"   {takeaway} — <i>{source}</i>")
    else:
        lines.append(f"   — <i>{source}</i>")
    if commentary:
        lines.append(f"   💡 {commentary}")
    return lines


def format_news_post(items: list[dict[str, Any]]) -> NewsPost:
    """News digest: rewritten headline + one-line takeaway, source-cited.

    Returns a ``NewsPost`` (a ``str``) whose ``.image_url`` is the first
    story's usable http(s) image — the digest's photo when the scheduler
    routes it through ``send_news_photo``. Items are the post-dicts shaped
    by ``channels.telegram_news._digest._article_to_post_dict`` (which
    already carries ``image_url``).
    """
    lines = ["📰 <b>RugMunch News</b>", "<i>The stories moving crypto right now.</i>", sep(), ""]
    for i, item in enumerate(items[:6], 1):
        lines.extend(_story_line(item, i))
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    image_url = ""
    for item in items[:6]:
        candidate = _pick_image_url(item.get("image_url"))
        if candidate:
            image_url = candidate
            break
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = image_url
    return post


def format_breaking_post(item: dict[str, Any]) -> NewsPost:
    """Solo breaking-news post: one high-importance story + official link.

    Used when the top ranked story carries a breaking marker (🚨/BREAKING/
    URGENT/JUST IN/FLASH). Links to the official source URL when one exists
    (falls back to the channel post URL), clips the summary, and keeps the
    same footer as the digest. The story's image (og:image / inline image,
    the post-dict's ``image_url``) is carried on the returned ``NewsPost``
    so the scheduler can attach it as a photo.
    """
    title = _esc(item.get("title") or "Untitled")
    url = (item.get("url") or "").strip()
    summary = _esc(_takeaway(item.get("summary") or item.get("description"), _BREAKING_SUMMARY_CHARS))
    commentary = _esc(_takeaway(item.get("commentary"), _COMMENTARY_HOOK_CHARS))
    source = _esc(item.get("source") or "unknown")
    headline = title if not url else f'<a href="{html.escape(url, quote=True)}">{title}</a>'
    lines = ["🚨 <b>BREAKING</b>", sep(), ""]
    lines.append(headline)
    if summary:
        lines.append("")
        lines.append(summary)
    if commentary:
        lines.append("")
        lines.append(f"💡 <b>Why it matters:</b> {commentary}")
    lines.append("")
    if url:
        lines.append(f'🔗 <i>Full story: <a href="{html.escape(url, quote=True)}">{source}</a></i>')
    else:
        lines.append(f"🔗 <i>Full story: {source}</i>")
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = _pick_image_url(item.get("image_url"))
    return post


def format_feature_post(item: dict[str, Any]) -> NewsPost:
    """Solo top-story card: one story with image, one-line hook + longer lede.

    Used for the "single story worth posting" case — a lone fresh story, or a
    high-importance (≥0.7) main-channel story without a breaking marker.
    Carries the rewritten title (``title`` = ``post_title`` when the LLM
    rewrite is enabled), a compelling one-line hook (first sentence of the
    body), a longer lede, the official link, and the story's image on the
    returned ``NewsPost`` so the scheduler attaches it as a photo.
    """
    title = _esc(item.get("title") or "Untitled")
    url = (item.get("url") or "").strip()
    summary = _strip_markup(item.get("summary") or item.get("description"))
    hook = _first_sentence(summary)
    commentary = _esc(_takeaway(item.get("commentary"), _COMMENTARY_HOOK_CHARS))
    lede = _lede_after_hook(summary, hook)
    source = _esc(item.get("source") or "unknown")
    headline = title if not url else f'<a href="{html.escape(url, quote=True)}">{title}</a>'
    lines = ["⭐ <b>Top Story</b>", sep(), ""]
    lines.append(headline)
    if hook:
        lines.append("")
        lines.append(_esc(hook))
    if commentary:
        lines.append("")
        lines.append(f"💡 <b>Why it matters:</b> {commentary}")
    if lede:
        lines.append("")
        lines.append(_esc(lede))
    lines.append("")
    if url:
        lines.append(f'🔗 <i>Full story: <a href="{html.escape(url, quote=True)}">{source}</a></i>')
    else:
        lines.append(f"🔗 <i>Full story: {source}</i>")
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = _pick_image_url(item.get("image_url"))
    return post


def format_hybrid_post(items: list[dict[str, Any]]) -> NewsPost:
    """Top-3 hybrid: lead story card (with image + hook) + numbered rest.

    A middle ground between the solo card and the full digest: the lead story
    gets the feature treatment (headline + one-line hook + image), then a
    short numbered list of the remaining stories (up to 4 more). ``image_url``
    is the first usable image among the items (lead preferred).
    """
    lead = items[0] if items else {}
    title = _esc(lead.get("title") or "Untitled")
    url = (lead.get("url") or "").strip()
    summary = _strip_markup(lead.get("summary") or lead.get("description"))
    hook = _first_sentence(summary)
    commentary = _esc(_takeaway(lead.get("commentary"), _COMMENTARY_HOOK_CHARS))
    source = _esc(lead.get("source") or "unknown")
    headline = title if not url else f'<a href="{html.escape(url, quote=True)}">{title}</a>'
    lines = ["📰 <b>RugMunch News</b>", sep(), ""]
    lines.append(f"⭐ {headline}")
    if hook:
        lines.append("")
        lines.append(_esc(hook))
    if commentary:
        lines.append("")
        lines.append(f"💡 <b>Why it matters:</b> {commentary}")
    if url:
        lines.append(f'   <i>— <a href="{html.escape(url, quote=True)}">{source}</a></i>')
    else:
        lines.append(f"   <i>— {source}</i>")
    rest = items[1:5]
    if rest:
        lines.append("")
        lines.append("More:")
        for i, item in enumerate(rest, 2):
            t = _esc(item.get("title") or "Untitled")
            u = (item.get("url") or "").strip()
            if u:
                t = f'<a href="{html.escape(u, quote=True)}">{t}</a>'
            s = _esc(item.get("source") or "unknown")
            lines.append(f"{i}. {t} — <i>{s}</i>")
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    image_url = _pick_image_url(lead.get("image_url"))
    for item in items[1:]:
        if not image_url:
            image_url = _pick_image_url(item.get("image_url"))
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = image_url
    return post


def format_themed_post(items: list[dict[str, Any]], theme: str = "") -> NewsPost:
    """Themed roundup: stories sharing one topic (e.g. ``$BTC``) under a label.

    Used when multiple stories in the batch mention the same ``$TOKEN`` (see
    ``channels.telegram_news._digest._shared_topic``). Numbered, source-cited
    headlines grouped under a theme header; carries the first usable image.
    """
    label = _esc(theme) if theme else "Top Stories"
    lines = [f"🧵 <b>RugMunch News · {label} Roundup</b>", sep(), ""]
    for i, item in enumerate(items[:6], 1):
        lines.extend(_story_line(item, i))
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    image_url = ""
    for item in items[:6]:
        candidate = _pick_image_url(item.get("image_url"))
        if candidate:
            image_url = candidate
            break
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = image_url
    return post


# ── Alerts channel ────────────────────────────────────────────────────────


def _scanner_url(address: str, chain: str) -> str:
    """RugMunch scanner report deep link for a token ("" when no address)."""
    if not address:
        return ""
    return f"https://rugmunch.io/scanner?token={address}&chain={chain or ''}"


def _explorer_url(address: str, chain: str) -> str:
    """Per-wallet explorer link for smart-money wallets ("" when unknown)."""
    if not address:
        return ""
    if (chain or "").lower() == "solana":
        return f"https://solscan.io/account/{address}"
    return ""


def _item_links(item: dict[str, Any], *, addr: str = "", chain: str = "") -> str:
    """One line of actionable links for a curated token item ("" when none).

    Prefers payload-supplied ``scanner_url``/``chart_url``; falls back to
    building the standard scan (rugmunch.io/scanner) and chart (GMGN) deep
    links from the token address + chain. ``explorer_url`` renders only when
    the payload carries one (wallet pages are not token pages). Fail-open.
    """
    address = str(item.get("address") or item.get("token_address") or addr or "")
    chain_name = str(item.get("chain") or chain or "")
    parts: list[str] = []
    scanner = str(item.get("scanner_url") or _scanner_url(address, chain_name)).strip()
    if scanner:
        parts.append(f'<a href="{html.escape(scanner, quote=True)}">🔍 Scan</a>')
    chart = str(item.get("chart_url") or "").strip()
    if not chart and address:
        chart = gmgn_url(address, chain_name)
    if chart:
        parts.append(f'<a href="{html.escape(chart, quote=True)}">📊 Chart</a>')
    explorer = str(item.get("explorer_url") or "").strip()
    if explorer:
        parts.append(f'<a href="{html.escape(explorer, quote=True)}">🧭 Explorer</a>')
    return " · ".join(parts)


#: Known scanner flag → one-line "why it matters" (most specific first).
_ALERT_VERDICT_REASONS: tuple[tuple[str, str], ...] = (
    ("honeypot", "honeypot — sells can be blocked after buying"),
    ("rugpull", "rugpull indicators — exit-liquidity risk"),
    ("rug_pull", "rugpull indicators — exit-liquidity risk"),
    ("rug_indicators", "rugpull indicators — exit-liquidity risk"),
    ("rug_pattern", "rug-pattern behavior — exit-liquidity risk"),
    ("lp_removed", "LP removed — liquidity can be yanked at any time"),
    ("liquidity_removed", "liquidity removed — holders cannot exit"),
    ("liquidity_low", "low liquidity — price is easy to manipulate"),
    ("low_liquidity", "low liquidity — price is easy to manipulate"),
    ("no_liquidity", "no liquidity — the token cannot be traded"),
    ("volume_fake", "fake volume — activity is wash-traded, not real"),
    ("wash_trad", "wash trading detected — volume is fabricated"),
    ("bundled_launch", "bundled launch — supply is concentrated at the top"),
    ("bundle_detect", "bundled launch — supply is concentrated at the top"),
    ("pump_dump", "pump-and-dump pattern — dump risk after the run"),
    ("malicious_deployer", "malicious deployer — the contract can be changed or rugged"),
    ("high_risk_deployer", "high-risk deployer — dev wallet is flagged"),
    ("suspicious_deployer", "suspicious deployer — dev wallet is flagged"),
    ("dev_high_risk", "high-risk deployer — dev wallet is flagged"),
    ("concentrated_holders", "concentrated holders — top wallets control the supply"),
    ("concentrated_lp", "concentrated LP — shallow, easily manipulated liquidity"),
    ("mintable", "mintable — supply can be inflated after launch"),
    ("sanctioned", "sanctioned entity — compliance and exit risk"),
    ("copycat", "copycat — impersonates a known token"),
    ("active_proxy_owner", "active proxy owner — contract logic can be swapped"),
    ("risk_level=critical", "critical risk verdict from the scanner"),
    ("risk_level=high", "high risk verdict from the scanner"),
)
_ALERT_VERDICT_FALLBACK = "flagged by the RugMunch scanner — verify before touching it"


#: Compact scanner-flag → why-it's-risky phrase for scan cards (specific first).
#: These are the HUMAN reasons a scan is notable — honeypot / bundle / fake
#: volume / LP removed / flagged deployer — surfaced instead of the generic
#: "high-risk verdict" line. Unknown flags stay raw so no signal is dropped.
_SCAN_REASON_BITS: tuple[tuple[str, str], ...] = (
    ("honeypot", "honeypot: sells can be blocked"),
    ("rugpull", "rug indicators: exit liquidity at risk"),
    ("rug_pull", "rug indicators: exit liquidity at risk"),
    ("rug_indicators", "rug indicators: exit liquidity at risk"),
    ("rug_pattern", "rug-pattern behavior"),
    ("lp_removed", "LP removed: holders can't exit"),
    ("liquidity_removed", "LP removed: holders can't exit"),
    ("liquidity_unlocked", "LP unlocked: can be yanked anytime"),
    ("lp_unlocked", "LP unlocked: can be yanked anytime"),
    ("liquidity_low", "thin liquidity: price is easy to manipulate"),
    ("low_liquidity", "thin liquidity: price is easy to manipulate"),
    ("no_liquidity", "no liquidity: token can't be traded"),
    ("volume_fake", "fake volume: wash-traded"),
    ("wash_trad", "fake volume: wash-traded"),
    ("bundled_launch", "bundled launch: supply concentrated"),
    ("bundle_detect", "bundled launch: supply concentrated"),
    ("bundle_suspect", "bundled launch: supply concentrated"),
    ("pump_dump", "pump & dump pattern"),
    ("malicious_deployer", "flagged deployer: dev can rug"),
    ("high_risk_deployer", "flagged deployer: dev can rug"),
    ("suspicious_deployer", "flagged deployer: dev can rug"),
    ("dev_high_risk", "flagged deployer: dev can rug"),
    ("concentrated_holders", "top wallets control the supply"),
    ("concentrated_lp", "concentrated LP: shallow"),
    ("mintable", "mintable: supply can be inflated"),
    ("mint_authority", "mint authority: supply can be inflated"),
    ("fresh_wallet", "fresh-wallet funded"),
    ("deployer_dump", "deployer dumping"),
    ("sanctioned", "sanctioned entity"),
    ("copycat", "copycat of a known token"),
    ("active_proxy_owner", "proxy owner: logic can swap"),
)


def _scan_reason_bits(item: dict[str, Any], *, limit: int = 3) -> list[str]:
    """Human "why it's risky" phrases for a scan card, from flags + security.

    Known flags map to their short reason (most specific first); unknown
    flags stay raw so no signal is dropped. The ``security`` sub-dict adds
    the LP-unlocked signal the flag rail does not carry. Falls back to the
    generic :func:`alert_verdict` when nothing specific is known. Pure —
    never raises.
    """
    bits: list[str] = []
    seen: set[str] = set()
    flags = [str(f).lower() for f in (item.get("risk_flags") or []) if f]
    consumed: set[str] = set()
    for flag in flags:
        for needle, reason in _SCAN_REASON_BITS:
            if needle in flag:
                if reason not in seen:
                    seen.add(reason)
                    bits.append(reason)
                consumed.add(flag)
                break
        if len(bits) >= limit:
            break
    security = item.get("security")
    if isinstance(security, dict) and security.get("lp_locked") is False:
        reason = "LP unlocked: can be yanked anytime"
        if reason not in seen and len(bits) < limit:
            seen.add(reason)
            bits.append(reason)
    for flag in flags:
        if flag in consumed or len(bits) >= limit:
            continue
        if flag not in seen:
            seen.add(flag)
            bits.append(flag)
    if not bits:
        bits.append(alert_verdict(item))
    return bits[:limit]


def alert_verdict(item: dict[str, Any]) -> str:
    """One-line "why it matters" for a scanner alert, derived from known flags.

    First known flag match wins (most specific patterns are listed first);
    unknown flags fall back to an honest generic line. Pure — never raises.
    """
    flags = [str(f).lower() for f in (item.get("risk_flags") or []) if f]
    for flag in flags:
        for needle, reason in _ALERT_VERDICT_REASONS:
            if needle in flag:
                return reason
    risk = str(item.get("risk_level") or "").lower()
    if risk in ("critical", "high"):
        return f"{risk}-risk verdict from the scanner — check before buying"
    return _ALERT_VERDICT_FALLBACK


def format_alerts_post(alerts: list[dict[str, Any]]) -> NewsPost:
    """Urgent risk digest: alert type, condition met vs current, why-it-matters.

    Each flagged token renders: the alert type, the token, the condition that
    was met vs the current scanner state (``condition``), a one-line
    why-it-matters verdict (derived from known risk flags), and actionable
    scan + chart links. Carries the first usable image on the returned
    ``NewsPost`` so the scheduler can attach it as a photo (text-only when
    none — fail-open).
    """
    lines = [
        "🚨 <b>Risk Alerts</b>",
        "<i>High-risk tokens auto-flagged this window — do not buy unvetted.</i>",
        sep(),
        "",
    ]
    if not alerts:
        lines.append("✅ No new high-risk tokens detected in the last window. All clear.")
        lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
        return NewsPost(_clip("\n".join(lines)))
    for item in alerts[:6]:
        symbol = _esc(item.get("symbol") or "???")
        name = _esc(item.get("name") or symbol)
        addr = str(item.get("address") or "")
        chain = _esc(item.get("chain") or "")
        risk = _esc(item.get("risk_level") or "unknown")
        alert_type = _esc(item.get("alert_type") or "scanner")
        lines.append(f"🛡️ <b>{alert_type.title()} Alert</b>")
        lines.append(f"{_risk_emoji(risk)} <b>{name}</b> ({symbol})")
        if addr:
            lines.append(f"<code>{_esc(addr)}</code>")
        condition = str(item.get("condition") or "").strip()
        if not condition:
            score = item.get("risk_score")
            if score is not None:
                with suppress(TypeError, ValueError):
                    condition = f"risk {float(score):.0f}/100 (flagged {risk})"
            if not condition:
                condition = f"flagged {risk}"
        lines.append(f"⚡ Condition met: {_esc(condition)}")
        lines.append(f"💡 Why it matters: {_esc(alert_verdict(item))}")
        flags = [f for f in item.get("risk_flags") or [] if f]
        if flags:
            lines.append("   ⚠️ " + " · ".join(_esc(f) for f in flags[:5]))
        if chain:
            lines.append(f"⛓️ {chain}")
        links = _item_links(item, addr=addr, chain=str(item.get("chain") or ""))
        if addr:
            scan_cmd = f"🔍 /scan <code>{_esc(addr)}</code>"
            links = f"{links} · {scan_cmd}" if links else scan_cmd
        if links:
            lines.append(links)
        lines.append("")
    lines.append("<i>Auto-flagged by the RugMunch scanner. Always /scan before buying anything on this list.</i>")
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    post = NewsPost(_clip("\n".join(lines)))
    for item in alerts[:6]:
        candidate = _pick_image_url(item.get("image_url"))
        if candidate:
            post.image_url = candidate
            break
    return post


def format_main_escalation(item: dict[str, Any]) -> NewsPost:
    """Solo @cryptorugmuncher card for a reranker-escalated channel item (≥0.7).

    Used by the alerts/alpha jobs when an item clears the main gate: a
    high-signal headline (token or signal name), the condition that was met,
    a one-line why-it-matters, and the actionable scan/chart links. Carries
    the item's image on the returned ``NewsPost`` when one exists (text-only
    otherwise). The item also posts to its native channel digest — this card
    is the additive main-channel surface only.
    """
    name = str(item.get("name") or item.get("title") or item.get("symbol") or "Signal").strip()
    symbol = str(item.get("symbol") or "").strip()
    headline = _esc(f"{name} ({symbol})".strip() if symbol else name)
    addr = str(item.get("address") or item.get("token_address") or "")
    scanner = str(item.get("scanner_url") or _scanner_url(addr, str(item.get("chain") or ""))).strip()
    if scanner:
        headline = f'<a href="{html.escape(scanner, quote=True)}">{headline}</a>'
    lines = ["🚨 <b>HIGH SIGNAL</b>", sep(), ""]
    lines.append(headline)
    if addr:
        lines.append(f"<code>{_esc(addr)}</code>")
    condition = str(item.get("condition") or "").strip()
    verdict = str(item.get("verdict") or item.get("summary") or "").strip()
    risk = str(item.get("risk_level") or "").lower()
    if not verdict and (item.get("risk_flags") or risk in ("critical", "high")):
        verdict = alert_verdict(item)
    bits: list[str] = []
    if condition:
        bits.append(f"⚡ <b>Condition met:</b> {_esc(condition)}")
    if verdict:
        bits.append(f"💡 <b>Why it matters:</b> {_esc(verdict)}")
    if bits:
        lines.append("")
        lines.extend(bits)
    chain = _esc(item.get("chain") or "")
    if chain:
        lines.append(f"⛓️ {chain}")
    links = _item_links(item, addr=addr, chain=str(item.get("chain") or ""))
    if links:
        lines.append("")
        lines.append(links)
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = _pick_image_url(item.get("image_url"))
    return post


# ── Scans channel ────────────────────────────────────────────────────────


def format_scan_card(item: dict[str, Any]) -> NewsPost:
    """Compact @rmiscans card: real reasons + risk score + token + links.

    One token per card (the scans job posts each notable scan as its own
    card, ~10/day). The reasons line surfaces the ACTUAL why a token is
    risky — honeypot / bundle / fake volume / LP removed / flagged deployer
    (``_scan_reason_bits``) — instead of the generic "high-risk verdict";
    the optional ``commentary`` key renders the one-line AI wit when present
    (fail-open to no line). Name/symbol render honestly: no "Unknown (???)"
    when only one of them is known. The score line renders the numeric risk
    (or falls back to the risk level when the score is absent), and the scan
    deep link always points at rugmunch.io/scanner. Carries the item's image
    when one exists (text-only otherwise — fail-open).
    """
    raw_name = str(item.get("name") or "").strip()
    raw_symbol = str(item.get("symbol") or "").strip()
    name = "" if _is_placeholder(raw_name) else raw_name
    symbol = "" if _is_placeholder(raw_symbol) else raw_symbol
    if name and symbol and symbol.lower() != name.lower():
        label = f"{_esc(name)} ({_esc(symbol)})"
    elif symbol:
        label = _esc(symbol)
    elif name:
        label = _esc(name)
    else:
        label = "Unnamed token"
    addr = str(item.get("address") or item.get("token_address") or "")
    chain = _esc(item.get("chain") or "")
    risk = str(item.get("risk_level") or "unknown").lower()
    score = item.get("risk_score")
    score_line = ""
    if score is not None:
        with suppress(TypeError, ValueError):
            score_line = f"{_risk_emoji(risk)} Risk {float(score):.0f}/100 — {risk}"
    if not score_line:
        score_line = f"{_risk_emoji(risk)} Verdict: {risk}"
    lines = ["🛡️ <b>Scan of note</b>", sep(), ""]
    lines.append(f"{_risk_emoji(risk)} <b>{label}</b>{f' · {chain}' if chain else ''}")
    if addr:
        lines.append(f"<code>{_esc(addr)}</code>")
    lines.append(score_line)
    reasons = _scan_reason_bits(item)
    if reasons:
        lines.append(f"{_risk_emoji(risk)} " + " · ".join(_esc(r) for r in reasons))
    commentary = str(item.get("commentary") or "").strip()
    if commentary:
        lines.append(f"💬 {_esc(commentary)}")
    links = _item_links(item, addr=addr, chain=str(item.get("chain") or ""))
    if links:
        lines.append("")
        lines.append(links)
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = _pick_image_url(item.get("image_url"))
    return post


# ── Alpha channel ─────────────────────────────────────────────────────────


def _fmt_usd(value: Any) -> str:
    with suppress(TypeError, ValueError):
        return f"${float(value):,.0f}"
    return ""


def _fmt_age_hours(value: Any) -> str:
    with suppress(TypeError, ValueError):
        hours = float(value)
        if hours < 1:
            return f"⏱ {int(hours * 60)}m old"
        return f"⏱ {hours:.0f}h old"
    return ""


def _fmt_wallet_short(addr: str) -> str:
    return short_addr(addr) if addr else "?"


def _launch_verdict(launch: dict[str, Any]) -> str:
    """One-line "why it matters" for a launch, honest to its scan verdict."""
    risk = str(launch.get("risk_level") or "unknown").lower()
    if risk == "critical":
        return "critical scan verdict — skip unless you know exactly what you are doing"
    if risk == "high":
        return "high scan verdict — verify liquidity and holders before sizing"
    if risk == "medium":
        return "medium scan verdict — check liquidity and holder concentration"
    if risk == "low":
        return "low scan verdict — still verify before buying"
    return "new launch — verify before buying"


def format_alpha_post(
    whale_moves: list[dict[str, Any]],
    smart_money: list[dict[str, Any]],
    launches: list[dict[str, Any]],
) -> NewsPost:
    """Alpha digest: whale moves + smart money wallets + new launches.

    High-signal voice: every item leads with the signal, adds the context
    that matters (whale-move body hook, wallet tags, launch age/liquidity),
    a one-line verdict, and actionable scan/chart/explorer links. Every
    section only renders when its live source returned data — an empty
    section is omitted, never invented. Carries the first usable image on
    the returned ``NewsPost`` so the scheduler can attach it as a photo.
    """
    lines = ["🐋 <b>Alpha Feed</b>", "<i>High-signal, low noise — /scan before you ape.</i>", sep(), ""]

    if whale_moves:
        lines.append("⚡ <b>Whale Moves</b>")
        for move in whale_moves[:4]:
            title = _esc(move.get("title") or "Whale move")
            chain = _esc(move.get("chain") or "solana")
            addr = str(move.get("token_address") or "")
            lines.append(f"• {title} ({chain})")
            body = _esc(_takeaway(move.get("body") or move.get("summary"), _ALPHA_TAKEAWAY_CHARS))
            if body:
                lines.append(f"   💬 {body}")
            if addr:
                lines.append(f"   🔍 /scan <code>{_esc(addr)}</code>")
            links = _item_links(move, addr=addr, chain=str(move.get("chain") or "solana"))
            if links:
                lines.append(f"   {links}")
        lines.append("")

    if smart_money:
        lines.append("🧠 <b>Smart Money Wallets</b>")
        for w in smart_money[:5]:
            label = _esc(w.get("label") or _fmt_wallet_short(str(w.get("address") or "")))
            addr = str(w.get("address") or "")
            pnl = _fmt_usd(w.get("pnl_usd") or w.get("realized_pnl"))
            bits = [f"<code>{_esc(addr)}</code>"]
            if pnl:
                bits.append(f"P&L {pnl}")
            lines.append(f"• <b>{label}</b> — " + " · ".join(bits))
            tags = [str(t) for t in (w.get("tags") or []) if str(t).strip()]
            if tags:
                lines.append("   🏷 " + " · ".join(_esc(t) for t in tags[:4]))
            explorer = _explorer_url(addr, str(w.get("chain") or "solana"))
            if explorer:
                lines.append(f'   🧭 <a href="{html.escape(explorer, quote=True)}">Explorer</a>')
        lines.append("")

    if launches:
        lines.append("🆕 <b>New Launches</b>")
        for launch in launches[:5]:
            symbol = _esc(launch.get("symbol") or "???")
            name = _esc(launch.get("name") or symbol)
            chain = _esc(launch.get("chain") or "")
            risk = _esc(launch.get("risk_level") or "unknown")
            addr = str(launch.get("address") or "")
            lines.append(f"• {_risk_emoji(risk)} <b>{name}</b> ({symbol}){f' — {chain}' if chain else ''}")
            lines.append(f"   💡 {_esc(_launch_verdict(launch))}")
            meta = []
            age = _fmt_age_hours(launch.get("age_hours"))
            if age:
                meta.append(age)
            liq = _fmt_usd(launch.get("liquidity_usd") or launch.get("liquidity"))
            if liq:
                meta.append(f"💧 {liq} liq")
            if meta:
                lines.append("   " + " · ".join(meta))
            if addr:
                lines.append(f"   🔍 /scan <code>{_esc(addr)}</code>")
            links = _item_links(launch, addr=addr, chain=str(launch.get("chain") or ""))
            if links:
                lines.append(f"   {links}")
        lines.append("")

    if not whale_moves and not smart_money and not launches:
        lines.append("No fresh alpha signals in this window — checking again soon.")
        lines.append("")

    lines.append(f"🕐 {_now_stamp()}")
    lines.append(footer_links())
    post = NewsPost(_clip("\n".join(lines)))
    for item in [*whale_moves[:4], *smart_money[:5], *launches[:5]]:
        candidate = _pick_image_url(item.get("image_url"))
        if candidate:
            post.image_url = candidate
            break
    return post


__all__ = [
    "NewsPost",
    "alert_verdict",
    "format_alerts_post",
    "format_alpha_post",
    "format_breaking_post",
    "format_feature_post",
    "format_hybrid_post",
    "format_main_escalation",
    "format_news_post",
    "format_scan_card",
    "format_themed_post",
]
