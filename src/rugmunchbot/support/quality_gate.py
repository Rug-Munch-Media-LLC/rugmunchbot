"""Pre-post quality gate — HARD gate on every channel posting path.

Wraps :mod:`rugmunchbot.support.content_lint` with an enforce/autofix/
regenerate/skip pipeline. Every poster that ships text to a Telegram
channel MUST call :func:`pre_post_quality_gate` immediately before the
send; a ``blocked`` result means the post does not go out (log + skip —
crap never ships).

Pipeline (deterministic except the optional LLM regeneration hook):

1. Lint the post (banned phrases, double links, missing image, template
   signature, bland commentary).
2. Auto-fix what is fixable: strip duplicate URLs (keep the first
   occurrence, keep anchor labels), attach the placeholder brand image
   when the post has none and placeholders are allowed. Re-lint.
3. Still failing and a ``regenerate`` coroutine was supplied → call it
   ONCE with the violations (stricter-prompt retry), auto-fix + re-lint
   the regenerated text.
4. Still failing → ``blocked=True``. Caller logs the violations and
   SKIPS the post.

Pure Python, no new deps; the lint itself never calls an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from rugmunchbot.support.content_lint import (
    BLAND_MIN_WORDS,
    Violation,
    canonical_url,
    lint_post,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

#: Brand placeholder attached to image-less posts so the main channel never
#: ships a naked text post. Must be a real, publicly served http(s) image.
#: 2026-08-31: the old /og/rugmunch-intel.png path serves text/html (SPA
#: fallback) — Telegram rejects it and every "placeholder" post silently
#: shipped text-only. /brand/banner.jpg is the real og:image the site
#: itself declares (verified 200 image/jpeg).
#: 2026-09-01 (operator doctrine): the placeholder is the PLATFORM BADGE v2
#: (rmi_badgev2.png, 1024x1024) — the old banner.jpg is the operator's
# personal X header and must never ship as site content. Logo used
# sparingly: only as the last-resort fallback, never as content.
PLACEHOLDER_IMAGE_URL: Final = "https://rugmunch.io/brand/rmi_badgev2.png"

_ANCHOR_RE: Final = re.compile(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_BARE_URL_RE: Final = re.compile(r"https?://[^\s)<>\"']+")


@dataclass
class GateResult:
    """Outcome of :func:`pre_post_quality_gate`."""

    #: True when the post may be sent (clean after lint/autofix/regen).
    ok: bool
    #: Text to send (possibly auto-fixed / regenerated).
    text: str
    #: Image to attach (possibly the placeholder).
    image_url: str
    #: Violations still outstanding when blocked (empty when ok).
    violations: list[Violation] = field(default_factory=list)
    #: Duplicate links were stripped / placeholder image attached.
    auto_fixed: bool = False
    #: The regenerate hook produced the shipped text.
    regenerated: bool = False
    #: True → do NOT send; log and skip.
    blocked: bool = False


def strip_duplicate_urls(text: str) -> str:
    """Remove every occurrence of a URL after its first, keeping labels.

    ``<a href="U">label</a>`` repeats collapse to their label text; bare
    URL repeats are dropped. First occurrence always wins. Pure.
    """
    out = str(text or "")
    if not out.strip():
        return out
    seen: set[str] = set()

    def _sub_anchor(m: re.Match[str]) -> str:
        url = canonical_url(m.group(1))
        if url in seen:
            return m.group(2)
        seen.add(url)
        return m.group(0)

    # Hold surviving anchors out of the bare-URL pass so their href
    # attributes are never stripped by the dedupe below.
    anchors: list[str] = []

    def _hold(m: re.Match[str]) -> str:
        anchors.append(_sub_anchor(m))
        return f"\x00ANCHOR{len(anchors) - 1}\x00"

    out = _ANCHOR_RE.sub(_hold, out)

    def _sub_bare(m: re.Match[str]) -> str:
        url = canonical_url(m.group(0))
        if url in seen:
            return ""
        seen.add(url)
        return m.group(0)

    out = _BARE_URL_RE.sub(_sub_bare, out)
    for idx, anchor in enumerate(anchors):
        out = out.replace(f"\x00ANCHOR{idx}\x00", anchor)
    return re.sub(r"[ \t]{2,}", " ", out)


async def pre_post_quality_gate(
    text: str,
    *,
    image_url: str = "",
    source: str = "",
    kind: str = "post",
    min_words: int = BLAND_MIN_WORDS,
    require_image: bool = True,
    allow_placeholder_image: bool = True,
    regenerate: Callable[[str, list[Violation]], Awaitable[str]] | None = None,
    logger: Any = None,
) -> GateResult:
    """Hard quality gate run BEFORE any channel send.

    Returns a :class:`GateResult`; ``ok=False`` means the caller must NOT
    send (log ``violations`` and skip). ``regenerate`` (when given) is an
    async ``(text, violations) -> str`` hook — the ONE allowed LLM retry
    with a stricter prompt.
    """
    text = str(text or "")
    image_url = str(image_url or "").strip()
    log = logger

    # 2026-09-01 HARD REJECT: model-collapse gibberish never ships and never
    # retries (the same lane that produced garbage will produce garbage).
    from rugmunchbot.support.content_lint import (
        looks_like_gibberish,
        looks_like_reasoning_leak,
    )

    _gib, _gib_reason = looks_like_gibberish(text)
    if _gib:
        if log is not None:
            log.warning(
                "quality_gate_gibberish_blocked source=%s kind=%s reason=%s",
                source or "<unknown>",
                kind,
                _gib_reason,
            )
        return GateResult(
            ok=False,
            text=text,
            image_url=image_url,
            violations=[
                Violation(
                    rule="gibberish",
                    phrase=_gib_reason[:60],
                    location="text",
                    detail="model-collapse output rejected",
                )
            ],
        )

    # 2026-09-02 HARD REJECT: raw LLM chain-of-thought (prompt-mirroring
    # drafting narration) never ships and never retries.
    _leak, _leak_reason = looks_like_reasoning_leak(text)
    if _leak:
        if log is not None:
            log.warning(
                "quality_gate_reasoning_leak_blocked source=%s kind=%s",
                source or "<unknown>",
                kind,
            )
        return GateResult(
            ok=False,
            text=text,
            image_url=image_url,
            violations=[
                Violation(
                    rule=_leak_reason,
                    phrase="prompt-mirroring narration",
                    location="text",
                    detail="LLM chain-of-thought rejected",
                )
            ],
            blocked=True,
        )

    def _lint(text: str, image_url: str, *, min_words: int) -> list[Violation]:
        violations = lint_post(
            {"text": text, "image_url": image_url, "slug": "gate"},
            min_words=min_words,
        )
        if not require_image:
            violations = [v for v in violations if v.rule != "missing-image"]
        return violations
        violations = lint_post(
            {"text": text, "image_url": image_url, "slug": "gate"},
            min_words=min_words,
        )
        if not require_image:
            violations = [v for v in violations if v.rule != "missing-image"]
        return violations

    def _fail(violations: list[Violation], *, fixed: bool, regen: bool) -> GateResult:
        if log is not None:
            summary = "; ".join(f"{v.rule}:{v.phrase}" for v in violations[:5])
            log.warning(
                "quality_gate_blocked source=%s kind=%s violations=%d [%s]",
                source or "<unknown>",
                kind,
                len(violations),
                summary[:200],
            )
        return GateResult(
            ok=False,
            text=text,
            image_url=image_url,
            violations=violations,
            auto_fixed=fixed,
            regenerated=regen,
            blocked=True,
        )

    violations = _lint(text, image_url, min_words=min_words)
    if not violations:
        return GateResult(ok=True, text=text, image_url=image_url)

    # ── Auto-fix pass ────────────────────────────────────────────────────
    fixed = False
    fixed_text = strip_duplicate_urls(text)
    if fixed_text != text:
        fixed = True
        text = fixed_text
    if not image_url.startswith(("http://", "https://")) and allow_placeholder_image:
        image_url = PLACEHOLDER_IMAGE_URL
        fixed = True
    violations = _lint(text, image_url, min_words=min_words)
    if not violations:
        if log is not None:
            log.info("quality_gate_autofixed source=%s kind=%s", source or "<unknown>", kind)
        return GateResult(ok=True, text=text, image_url=image_url, auto_fixed=fixed)

    # ── Regeneration retry (once) ────────────────────────────────────────
    if regenerate is not None:
        try:
            regen_text = await regenerate(text, violations)
        except Exception as exc:
            if log is not None:
                log.warning("quality_gate_regen_failed err=%s", str(exc)[:120])
            regen_text = ""
        regen_text = strip_duplicate_urls(str(regen_text or ""))
        if regen_text.strip():
            regen_violations = _lint(regen_text, image_url, min_words=min_words)
            if not regen_violations:
                if log is not None:
                    log.info(
                        "quality_gate_regenerated source=%s kind=%s",
                        source or "<unknown>",
                        kind,
                    )
                return GateResult(
                    ok=True,
                    text=regen_text,
                    image_url=image_url,
                    auto_fixed=fixed,
                    regenerated=True,
                )
            violations = regen_violations

    return _fail(violations, fixed=fixed, regen=regenerate is not None)


__all__ = [
    "PLACEHOLDER_IMAGE_URL",
    "GateResult",
    "pre_post_quality_gate",
    "strip_duplicate_urls",
]
