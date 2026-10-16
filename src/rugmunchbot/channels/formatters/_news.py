from __future__ import annotations

import html
from typing import Any

from rugmunchbot.channels.formatters._primitives import (
    _BREAKING_SUMMARY_CHARS,
    _COMMENTARY_HOOK_CHARS,
    NewsPost,
    _clip,
    _esc,
    _first_sentence,
    _graphic_for,
    _lede_after_hook,
    _now_stamp,
    _pick_image_url,
    _scan_channel_shill,
    _strip_markup,
    _takeaway,
    feed_site_line,
    site_news_line,
)
from rugmunchbot.formatters import footer_links, sep


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
    """News digest: rewritten headline + one-line takeaway, source-cited, with bot CTA.

    Returns a ``NewsPost`` (a ``str``) whose ``.image_url`` is the first
    story's usable http(s) image — the digest's photo when the scheduler
    routes it through ``send_news_photo``. Items are the post-dicts shaped
    by ``channels.telegram_news._digest._article_to_post_dict`` (which
    already carries ``image_url``). Every post carries a bot CTA so readers
    discover @RugMunchBot.
    """
    lines = [
        "\U0001f4f0 <b>RugMunch News</b>",
        "<i>The stories moving crypto right now \u2014 /chat any story with @RugMunchBot</i>",
        sep(),
        "",
    ]
    for i, item in enumerate(items[:6], 1):
        lines.extend(_story_line(item, i))
    lines.extend(["", f"\U0001f550 {_now_stamp()}"])
    lines.append(site_news_line())
    lines.append(feed_site_line())
    lines.append(sep())
    lines.append(_scan_channel_shill())
    image_url = ""
    for item in items[:6]:
        candidate = _pick_image_url(item.get("image_url"))
        if candidate:
            image_url = candidate
            break
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = image_url or _graphic_for("news")
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
    summary = _esc(
        _takeaway(item.get("summary") or item.get("description"), _BREAKING_SUMMARY_CHARS)
    )
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
    lines.extend(["", f"🕐 {_now_stamp()}"])
    lines.append(site_news_line())
    lines.append(feed_site_line())
    lines.append(footer_links())
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = _pick_image_url(item.get("image_url")) or _graphic_for("breaking")
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
    lines.extend(["", f"\U0001f550 {_now_stamp()}"])
    lines.append(site_news_line())
    lines.append(feed_site_line())
    lines.append(footer_links())
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = _pick_image_url(item.get("image_url")) or _graphic_for("feature")
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
    lines = [
        "\U0001f4f0 <b>RugMunch News</b>",
        "<i>The stories moving crypto right now \u2014 /chat any story with @RugMunchBot</i>",
        sep(),
        "",
    ]
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
    lines.extend(["", f"🕐 {_now_stamp()}"])
    lines.append(site_news_line())
    lines.append(feed_site_line())
    lines.append(footer_links())
    image_url = _pick_image_url(lead.get("image_url"))
    for item in items[1:]:
        if not image_url:
            image_url = _pick_image_url(item.get("image_url"))
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = image_url or _graphic_for("hybrid")
    return post


def format_themed_post(items: list[dict[str, Any]], theme: str = "") -> NewsPost:
    """Themed roundup: stories sharing one topic (e.g. ``$BTC``) under a label.

    Used when multiple stories in the batch mention the same ``$TOKEN`` (see
    ``channels.telegram_news._digest._shared_topic``). Numbered, source-cited
    headlines grouped under a theme header; carries the first usable image.
    """
    label = _esc(theme) if theme else "Top Stories"
    lines = [
        f"\U0001f9f5 <b>RugMunch News \u00b7 {label} Roundup</b>",
        "<i>The stories moving crypto right now \u2014 /chat any story with @RugMunchBot</i>",
        sep(),
        "",
    ]
    for i, item in enumerate(items[:6], 1):
        lines.extend(_story_line(item, i))
    lines.extend(["", f"🕐 {_now_stamp()}"])
    lines.append(site_news_line())
    lines.append(feed_site_line())
    lines.append(footer_links())
    image_url = ""
    for item in items[:6]:
        candidate = _pick_image_url(item.get("image_url"))
        if candidate:
            image_url = candidate
            break
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = image_url or _graphic_for("themed")
    return post


def format_scam_digest(items: list[dict[str, Any]]) -> NewsPost:
    """Scam-call / shill-intel digest for the ALERTS channel (@rmialerts).

    Compact, high-signal card for the tg_news routed lane: each scam-call
    item renders its headline (linking the source — cite-or-omit), the
    pushing channel, and a clipped one-line takeaway. The alerts doctrine
    admits scam/rug detections, so this digest shares the alerts channel —
    never the news channel. Carries the first usable image on the returned
    ``NewsPost`` (text-only when none — fail-open).
    """
    lines = [
        "🚨 <b>Scam Calls &amp; Shill Intel</b>",
        "<i>Coordinated scam-call signals from watched channels \u2014 verify before ape: /scan @rugmunchbot</i>",
        sep(),
        "",
    ]
    for i, item in enumerate(items[:6], 1):
        title = _esc(item.get("title") or "Untitled")
        url = (item.get("url") or "").strip()
        if url:
            title = f'<a href="{html.escape(url, quote=True)}">{title}</a>'
        source = _esc(item.get("source") or item.get("source_channel") or "unknown")
        lines.append(f"{i}. {title} — <i>{source}</i>")
        takeaway = _esc(_takeaway(item.get("summary") or item.get("description"), 140))
        if takeaway:
            lines.append(f"   {takeaway}")
    lines.extend(["", f"🕐 {_now_stamp()}"])
    lines.append(site_news_line())
    lines.append(feed_site_line())
    lines.append(sep())
    lines.append(_scan_channel_shill())
    image_url = ""
    for item in items[:6]:
        candidate = _pick_image_url(item.get("image_url"))
        if candidate:
            image_url = candidate
            break
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = image_url or _graphic_for("scam_alert")
    return post
