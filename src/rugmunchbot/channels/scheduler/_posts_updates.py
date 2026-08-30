"""@rmiupdates changelog — daily system-progress digest (strategy v2).

The ``updates`` channel role existed in the registry with NO consumer
("mapped, no consumer yet"). This module is the consumer: one post per day
telling operators + the community what the system actually did and that it
is healthy:

* SHIPPED — conventional commits from our own product repos (rugmunchbot,
  rugbrain) in the last 24h, grouped feat/fix/perf/security, capped.
* PIPELINE HEALTH — rugbrain API, model gateway, the deep 4B embedder and
  the 4B reranker probed concurrently (2s budget each); ✅/❌ per service.
* FEED MIRROR — posts.json size + last-write age (the W30 mandatory mirror).

Fail-open everywhere: git unavailable → "no commits surfaced"; a probe
error → ❌ for that service; any compose error → no post this day. The
digest dedupes per calendar day so an accidental double-fire cannot spam.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from rugmunchbot.channels.scheduler._core import (
    _chat_id,
    _release,
    _remember,
    _safe_send,
    logger,
    post_counters_today,
)

#: Repos whose commits surface as "shipped". Comma-separated env override.
#: ``_THIS_REPO`` stays a Path — parents[4] is the repo root (parents[3] is
#: ``src/``); the str() cast in the earlier fix broke ``.parent`` resolution.
_THIS_REPO = Path(__file__).resolve().parents[4]
_DEFAULT_REPO_DIRS = f"{_THIS_REPO},{_THIS_REPO.parent / 'rugbrain'}"
REPO_DIRS = [
    d.strip()
    for d in os.getenv("RMI_CHANGELOG_REPO_DIRS", _DEFAULT_REPO_DIRS).split(",")
    if d.strip()
]

#: Service probes: label → URL. All are local/loopback model+API services.
#: 2026-08-29: the 0.6B speed lane (:8007/:8008) is deleted — probes cover
#: the canonical 4B lane (:8004 embed / :8003 rerank) only.
SERVICE_PROBES: dict[str, str] = {
    "rag-api": os.getenv("RAG_API_HEALTH_URL", "http://127.0.0.1:8012/health"),
    "model-gateway": os.getenv("GATEWAY_HEALTH_URL", "http://127.0.0.1:8010/health"),
    "embed-deep-4b": os.getenv("DEEP_EMBED_URL", "http://127.0.0.1:8004") + "/health",
    "rerank-4b": os.getenv("DEEP_RERANK_URL", "http://127.0.0.1:8003") + "/health",
}

FEED_DATA = os.path.join(os.getenv("RMI_FEED_DIR", "/var/www/feed.rugmunch.io"), "data")

_MAX_COMMIT_LINES = 8
_PROBE_TIMEOUT = 2.5


#: Posting-volume guard: watched editorial channel roles.
_WATCHED_ROLES: tuple[str, ...] = ("main", "news", "alerts", "alpha", "scans")

#: Secret-looking tokens scrubbed from changelog text (defense in depth —
#: commit subjects should never carry them, but a leaked-looking string must
#: never be echoed to a public channel). "No secrets" is a hard contract.
_SECRET_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"(?:sk|pk|key|api[_-]?key|token|secret|password|passwd|mnemonic|private[_-]?key)"
    r"[=:\s]+[A-Za-z0-9_\-\.]{12,}"
    r")"
    r"|(?:0x[a-fA-F0-9]{40,})"
    r"|(?:[A-Za-z0-9+/]{40,}={0,2})"  # base64-ish blob
    r"|(?:\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b)"  # IPv4
    r"|(?:tg[A-Za-z0-9_-]{20,})"  # telegram bot tokens
    r"|(?:https?://[^\s:@/]+:[^\s@/]+@)"  # creds-in-url
)


def _scrub_secrets(text: str) -> str:
    """Replace secret-looking substrings with a redaction marker."""
    return _SECRET_RE.sub("[REDACTED]", text or "")


def _humanize_subject(subject: str) -> str:
    """Turn a conventional-commit subject into a human-readable line.

    ``"feat(rag): dual-tier embedder contracts"`` →
    ``"✨ rag — dual-tier embedder contracts"`` (scope preserved, hyphens
    for the human eye). Non-conventional subjects pass through unchanged
    (scrubbed). Pure — never raises.
    """
    text = _scrub_secrets(subject or "").strip()
    m = re.match(
        r"^(feat|fix|perf|security|docs|refactor|chore|ops|style|build|ci|test|revert)\(([^)]+)\):\s*(.+)$",
        text,
    )
    if m:
        return f"{m.group(1)} — {m.group(2)} — {m.group(3).strip()}"
    m = re.match(
        r"^(feat|fix|perf|security|docs|refactor|chore|ops|style|build|ci|test|revert):\s*(.+)$",
        text,
    )
    if m:
        return f"{m.group(1)} — {m.group(2).strip()}"
    return text


def _git_commits_since(repo_dir: str, since: str) -> list[str]:
    """One-line conventional commits since *since* (e.g. "24 hours ago").

    Fail-open: missing repo / no git → []. Only the subject line is used;
    scopes are preserved ("feat(rag): …").
    """
    try:
        out = subprocess.run(  # noqa: S603 — fixed args, no shell, read-only log
            ["/usr/bin/git", "-C", repo_dir, "log", "--since=" + since, "--pretty=%s"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _group_commits(subjects: list[str]) -> dict[str, list[str]]:
    """Group commit subjects by conventional type; unknown → other."""
    groups: dict[str, list[str]] = {"feat": [], "fix": [], "perf": [], "security": [], "other": []}
    for subject in subjects:
        lowered = subject.lower()
        for key in ("feat", "fix", "perf", "security"):
            if lowered.startswith((key + "(", key + ":", key + "!")):
                groups[key].append(subject)
                break
        else:
            groups["other"].append(subject)
    return groups


def _render_shipped(groups: dict[str, list[str]]) -> list[str]:
    """Bounded markdown lines for the SHIPPED section (human-readable).

    Commit subjects are humanized (``feat(scope): msg`` → ``feat — scope —
    msg``) and secret-scrubbed before render — the changelog reads like a
    progress note, never raw git output, and never leaks credentials.
    """
    icons = {"feat": "✨", "fix": "🛠", "perf": "⚡", "security": "🔒", "other": "•"}
    lines: list[str] = []
    total = 0
    for key in ("feat", "security", "fix", "perf", "other"):
        for subject in groups[key]:
            if total >= _MAX_COMMIT_LINES:
                remaining = sum(len(v) for v in groups.values()) - total
                if remaining > 0:
                    lines.append(f"  …and {remaining} more")
                return lines
            lines.append(f"  {icons[key]} {_humanize_subject(subject)[:90]}")
            total += 1
    return lines


async def _probe_services() -> list[str]:
    """Concurrent health probes → '✅ label' / '❌ label' lines."""
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:

        async def _one(label: str, url: str) -> str:
            try:
                r = await client.get(url)
                mark = "✅" if r.status_code < 500 else "❌"
            except Exception as exc:
                logger.debug("updates:status_check_failed error=%s", str(exc)[:100])
                mark = "❌"
            return f"{mark} {label}"

        return list(await asyncio.gather(*(_one(k, u) for k, u in SERVICE_PROBES.items())))


def _feed_mirror_line() -> str:
    """feed.rugmunch.io mirror status line (fail-open)."""
    try:
        path = os.path.join(FEED_DATA, "posts.json")
        stat = os.stat(path)
        age_h = max((time.time() - stat.st_mtime) / 3600.0, 0.0)
        with open(path) as f:
            count = len(json.load(f))
        return (
            f"{'✅' if age_h < 48 else '⚠️'} feed mirror: {count} posts, last write {age_h:.0f}h ago"
        )
    except Exception:
        return "❌ feed mirror: unreadable"


def _posting_volume_lines() -> list[str]:
    """Per-channel successful-send counts for today (strategy v2 silence
    visibility). Chat ids map back to role names via the config registry;
    unknown chat ids (DMs, escalations) render as their short id. A channel
    role with ZERO posts all day renders ❌ — silence must be visible, not
    merely absent from the list."""
    try:
        from rugmunchbot.config import _resolve_channels

        channels = _resolve_channels()
    except Exception:
        channels = {}
    id_to_role = {v: k for k, v in channels.items()}
    counters = post_counters_today()
    role_counts: dict[str, int] = {}
    for chat_id, count in counters.items():
        role_counts[id_to_role.get(chat_id, chat_id[-6:])] = (
            role_counts.get(id_to_role.get(chat_id, chat_id[-6:]), 0) + count
        )
    # Every editorial channel role appears — silent ones flagged.
    lines: list[str] = []
    total = 0
    for role in _WATCHED_ROLES:
        count = role_counts.pop(role, 0)
        total += count
        marker = "✅" if count else "❌ silent"
        lines.append(f"  {marker} {role}: {count}")
    for label, count in sorted(role_counts.items(), key=lambda kv: -kv[1]):
        total += count
        lines.append(f"  {label}: {count}")
    lines.append(f"  total: {total}")
    return lines


def compose_updates_digest(health_lines: list[str] | None = None) -> str:
    """Build the daily changelog text. ``health_lines`` are pre-probed service
    statuses (the async poster awaits them); ``None`` degrades that section
    to a note (pure/test path). Individual section failures degrade to a
    note — a broken section never kills the digest."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    sections: list[str] = [f"🧠 RugMunch system progress — {today}", ""]

    subjects: list[str] = []
    for repo in REPO_DIRS:
        subjects.extend(_git_commits_since(repo, "24 hours ago"))
    sections.append(f"<b>Shipped ({len(subjects)} commits/24h)</b>")
    shipped_lines = _render_shipped(_group_commits(subjects))
    sections.extend(shipped_lines if shipped_lines else ["  (no commits surfaced)"])
    sections.append("")

    sections.append("<b>Pipeline health</b>")
    if health_lines is None:
        health_lines = ["(probes skipped)"]
    sections.extend(f"  {line}" for line in health_lines)
    sections.append(f"  {_feed_mirror_line()}")
    sections.append("")
    sections.append("<b>Posting volume (today, UTC)</b>")
    sections.extend(_posting_volume_lines())

    return "\n".join(sections)


async def post_updates_digest(ctx: Any) -> int:
    """Post the daily changelog to @rmiupdates. Returns 1 when posted.

    Dedupe key includes the calendar date so re-fires the same day are
    no-ops; a failed send releases the mark so a later retry can deliver.
    """
    chat_id = _chat_id("updates")
    if not chat_id:
        # 2026-08-30: the ``updates`` role has fallback=None and no env/gopass
        # id in production, so this digest silently no-ops every day at info
        # level. Loud warn so an unconfigured @rmiupdates is actionable, not
        # buried — silence must be visible, not merely absent.
        logger.warning(
            "channels:updates_no_chat_id",
            job="channel_updates",
            reason="set RMI_UPDATES_CHANNEL_ID or gopass CHANNEL_RMI_UPDATES",
        )
        return 0
    today = datetime.now(UTC).strftime("%Y%m%d")
    dedupe_key = f"updates:{today}"
    if not _remember("updates", dedupe_key):
        return 0
    try:
        health_lines = await _probe_services()
    except Exception:
        health_lines = ["❌ probe failure"]
    text = compose_updates_digest(health_lines)
    ok = await _safe_send(ctx.bot, chat_id, text)
    if not ok:
        _release("updates", dedupe_key)
        return 0
    logger.info("channels:updates posted date=%s", today)
    return 1


__all__ = ["compose_updates_digest", "post_updates_digest"]
