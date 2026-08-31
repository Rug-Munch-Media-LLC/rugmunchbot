"""Deterministic content-quality lint (style-guide enforcement, no LLM).

Canonical home of the banned-phrase / anti-slop rules that were previously
duplicated as prompt fragments (``channels/style_guides.py``) and as a private
tuple (``channels/scheduler/_posts_roundup.py``). Pure Python, zero deps,
deterministic — safe to run in CI and in the weekly audit job.

Checks:
* ``banned-phrase``      — AI-slop / banned phrases (case-insensitive).
* ``double-link``        — the same URL appearing more than once in a post.
* ``missing-image``      — feed-store post dicts without an http(s) image.
* ``template-signature`` — the rugbrain fallback writer's "[SEV] Title" shape
                           (template output = slop, per CONTENT-POSTING-SPEC).
* ``bland-commentary``   — fewer than ``BLAND_MIN_WORDS`` words of original
                           commentary (URLs/links/hashtags stripped) or text
                           that is mostly a bare headline + link.

Import-linter note: this module lives in ``support`` (a foundation package)
so both ``channels`` and ``tools`` may import it — ``tools`` is forbidden
from importing ``channels``.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Final

#: Banned phrases (style_guides.py anti-slop lists + daily_market_summary
#: SYSTEM_PROMPT). Canonical list — ``_posts_roundup`` imports this instead of
#: keeping its own copy, so weakening the guide here weakens it everywhere.
BANNED_PHRASES: Final[tuple[str, ...]] = (
    "stay safe out there",
    "nfa",
    "not financial advice",
    "dyor",
    "gm",
    "wagmi",
    "mooning",
    "wen lambo",
    "hodl",
    "delve into",
    "ever-evolving landscape",
    "ever-evolving world",
    "the crypto landscape",
    "robust",
    "revolutionary",
    "paradigm shift",
    "the crypto space is buzzing",
    "staying ahead of the curve",
    "the future of finance",
    "it's worth noting",
    "at the end of the day",
    "stay tuned",
    "in the ever-evolving",
)

#: Signature of the rugbrain ``_template_post`` fallback writer: "[SEV] Title"
#: first line + "Source: …" footer. Template output = DO NOT POST (spec law).
TEMPLATE_SIGNATURE_RE: Final = re.compile(
    r"^\[(?:CRITICAL|HIGH|MEDIUM|LOW)\]\s+.+\n\nSource:\s+", re.DOTALL
)

#: Bland-commentary heuristic: fewer than this many words of original
#: commentary (after stripping URLs / HTML / link labels) is flagged.
BLAND_MIN_WORDS: Final = 20

_URL_RE: Final = re.compile(r"https?://\S+")
_HTML_RE: Final = re.compile(r"<[^>]+>")
_WORD_RE: Final = re.compile(r"[A-Za-z0-9'\-]+")


@dataclass(frozen=True)
class Violation:
    """One lint finding: rule id, matched phrase/reason, and location."""

    rule: str
    phrase: str
    location: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.location}: [{self.rule}] {self.phrase} — {self.detail}"


def _find_phrase(text: str, phrase: str) -> int:
    """Case-insensitive index of *phrase* in *text* (-1 when absent)."""
    return text.lower().find(phrase.lower())


def _line_col(text: str, offset: int) -> tuple[int, int]:
    """1-based (line, column) for a character offset."""
    line = text.count("\n", 0, offset) + 1
    col = offset - (text.rfind("\n", 0, offset) + 1) + 1
    return line, col


def lint_text(text: str) -> list[Violation]:
    """Lint a single post body. Returns violations (empty = clean).

    Deterministic and pure — no network, no LLM.
    """
    violations: list[Violation] = []
    raw = str(text or "")
    if not raw.strip():
        return violations

    for phrase in BANNED_PHRASES:
        idx = _find_phrase(raw, phrase)
        if idx < 0:
            continue
        # Short single-token phrases ("gm", "nfa", "dyor", "hodl") must match
        # on word boundaries only — a plain substring match false-positives
        # inside words like "rugmunchbot" or "programmatic". Multi-word
        # phrases keep plain substring semantics (matches the roundup's
        # historical behavior for the long slop phrases).
        if (
            " " not in phrase
            and len(phrase) <= 5
            and not re.search(rf"\b{re.escape(phrase)}\b", raw, re.IGNORECASE)
        ):
            continue
        line, col = _line_col(raw, idx)
        violations.append(
            Violation(
                rule="banned-phrase",
                phrase=phrase,
                location=f"line {line}, col {col}",
                detail="banned phrase (style guide anti-slop list)",
            )
        )

    # Double-linking: the same URL must never appear twice in one post.
    urls = re.findall(r"https?://[^\s)>\"']+", raw)
    seen: dict[str, int] = {}
    for url in urls:
        key = url.rstrip(".,;:")
        seen[key] = seen.get(key, 0) + 1
    for url, count in seen.items():
        if count > 1:
            line, col = _line_col(raw, raw.find(url))
            violations.append(
                Violation(
                    rule="double-link",
                    phrase=url,
                    location=f"line {line}, col {col}",
                    detail=f"URL appears {count}x in one post",
                )
            )

    if TEMPLATE_SIGNATURE_RE.match(raw):
        violations.append(
            Violation(
                rule="template-signature",
                phrase="[SEV] Title + Source footer",
                location="line 1",
                detail="matches the rugbrain template fallback writer signature",
            )
        )

    # Bland-commentary heuristic: strip URLs, HTML tags and link labels,
    # then count remaining words of original commentary.
    commentary = _URL_RE.sub(" ", raw)
    commentary = re.sub(r"<a\s+href=\"[^\"]*\">(.*?)</a>", r"\1", commentary)
    commentary = _strip_html(commentary)
    words = _WORD_RE.findall(commentary)
    if len(words) < BLAND_MIN_WORDS:
        violations.append(
            Violation(
                rule="bland-commentary",
                phrase=f"{len(words)} words",
                location="post",
                detail=f"fewer than {BLAND_MIN_WORDS} words of original commentary",
            )
        )

    return violations


def _strip_html(text: str) -> str:
    return _HTML_RE.sub(" ", text)


def lint_post(post: dict[str, Any]) -> list[Violation]:
    """Lint a feed-store post dict (slug/ts/text/source/kind/image_url).

    Adds the ``missing-image`` check on top of :func:`lint_text`. Posts with
    no image field at all are treated as image-less (flagged); an explicit
    empty/relative image is flagged too.
    """
    violations = lint_text(str(post.get("text") or ""))
    image = str(post.get("image_url") or post.get("image") or "").strip()
    if not image.startswith(("http://", "https://")):
        slug = str(post.get("slug") or "<unknown>")
        violations.append(
            Violation(
                rule="missing-image",
                phrase=slug,
                location="post",
                detail="no http(s) image_url on feed post",
            )
        )
    return violations


def lint_posts(posts: list[dict[str, Any]]) -> dict[str, list[Violation]]:
    """Lint every post in a feed store; returns {slug: violations} (only posts
    with at least one violation are included)."""
    out: dict[str, list[Violation]] = {}
    for post in posts:
        if not isinstance(post, dict):
            continue
        slug = str(post.get("slug") or f"idx-{len(out)}")
        v = lint_post(post)
        if v:
            out[slug] = v
    return out


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m rugmunchbot.tools.content_lint <file-or-text>``.

    Exits 0 when clean, 1 when violations found, 2 on usage/IO errors.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m rugmunchbot.tools.content_lint",
        description="Deterministic style-guide lint for bot content.",
    )
    parser.add_argument("target", help="path to a file, or a literal text string")
    parser.add_argument(
        "--posts",
        action="store_true",
        help="treat the file as a feed-store posts.json (list of post dicts) "
        "and lint each post's text field (adds missing-image check)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    args = parser.parse_args(args=argv)

    import os

    per_post: dict[str, list[Violation]] = {}
    violations: list[Violation] = []
    source = args.target

    if os.path.isfile(args.target):
        with open(args.target, encoding="utf-8") as f:
            content = f.read()
        if args.posts:
            try:
                loaded = json.loads(content)
            except json.JSONDecodeError:
                print(f"content-lint: {args.target} is not valid JSON", file=sys.stderr)
                return 2
            posts = (
                list(loaded.get("posts") or []) if isinstance(loaded, dict) else list(loaded or [])
            )
            per_post = lint_posts(posts)
            violations = [v for vs in per_post.values() for v in vs]
        else:
            violations = lint_text(content)
    else:
        source = "<text>"
        violations = lint_text(args.target)

    if args.json:
        report: dict[str, Any] = {
            "source": source,
            "clean": not violations,
            "violation_count": len(violations),
        }
        if per_post:
            report["posts"] = {
                slug: [
                    {
                        "rule": v.rule,
                        "phrase": v.phrase,
                        "location": v.location,
                        "detail": v.detail,
                    }
                    for v in vs
                ]
                for slug, vs in per_post.items()
            }
        else:
            report["violations"] = [
                {
                    "rule": v.rule,
                    "phrase": v.phrase,
                    "location": v.location,
                    "detail": v.detail,
                }
                for v in violations
            ]
        print(json.dumps(report, indent=2))
    else:
        if per_post:
            for slug, vs in per_post.items():
                for v in vs:
                    print(f"{source}:{slug}:{v.location}: [{v.rule}] {v.phrase} — {v.detail}")
        else:
            for v in violations:
                print(f"{source}:{v.location}: [{v.rule}] {v.phrase} — {v.detail}")
        print(f"content-lint: {len(violations)} violation(s)" + ("" if violations else " — clean"))
    return 1 if violations else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
