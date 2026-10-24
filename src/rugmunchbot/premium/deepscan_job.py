#!/usr/bin/env python3
"""On-demand forensic scan: progress, cancel, and the scanner's tier contract.

A deep scan is long (20+ modules, minutes), so the user needs three things the
fire-and-forget path cannot give: a progress line, a way out, and an honest
statement of what their tier is allowed to run.

The *module tiers* mirror the backend scanner (``CORE``, ``DEEP``, ``PREMIUM``,
``INTEL``, ``SENTINEL``). We do not re-implement the backend's rules — we
declare which of its tiers a subscription unlocks, so the progress card can
say "stopped at PREMIUM (needs Hunter)" instead of silently returning less.

State is an in-process registry keyed by job id with one live job per user:
a second ``/deepscan`` supersedes the first rather than running two.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from rugmunchbot.premium.gating import rank_of

#: Backend scanner module tiers, cheapest first.
MODULE_TIERS: tuple[str, ...] = ("CORE", "DEEP", "PREMIUM", "INTEL", "SENTINEL")

#: Subscription rank required to run each module tier.
#: free = CORE; scout = DEEP; hunter = PREMIUM; pro = INTEL + SENTINEL.
UNLOCK: dict[str, int] = {"CORE": 0, "DEEP": 1, "PREMIUM": 2, "INTEL": 3, "SENTINEL": 3}

#: Progress steps shown to the user, in order.
STAGES: tuple[str, ...] = (
    "queued",
    "contract",
    "holders",
    "liquidity",
    "wallet-graph",
    "social",
    "verdict",
)

CANCELLED = "cancelled"
DONE = "done"
RUNNING = "running"


@dataclass
class ScanJob:
    """One interactive scan and the slot it occupies."""

    job_id: str
    user_id: int
    address: str
    chain: str
    stage: str = STAGES[0]
    modules: tuple[str, ...] = ()
    cancelled: bool = False
    created_at: float = field(default_factory=time.time)

    @property
    def progress(self) -> float:
        """Fraction complete, derived from the stage index."""
        index = STAGES.index(self.stage) if self.stage in STAGES else 0
        return round(index / (len(STAGES) - 1), 2)

    @property
    def status(self) -> str:
        """``running`` / ``cancelled`` / ``done`` — never inferred by callers."""
        if self.cancelled:
            return CANCELLED
        return DONE if self.stage == STAGES[-1] else RUNNING


class JobRegistry:
    """One live scan per user; cancel is a flag the runner polls.

    The registry holds no locks and no threads: the runner is a coroutine and
    the cancel path only flips a boolean, which is the whole point — a cancel
    must never wait on the work it is cancelling.
    """

    def __init__(self) -> None:
        self._by_user: dict[int, ScanJob] = {}
        self._by_id: dict[str, ScanJob] = {}

    def start(self, user_id: int, address: str, chain: str, tier: str) -> ScanJob:
        """Open a job, superseding (and cancelling) any job the user already has."""
        previous = self._by_user.get(user_id)
        if previous is not None and previous.status == RUNNING:
            previous.cancelled = True
        job = ScanJob(
            job_id=uuid.uuid4().hex[:12],
            user_id=user_id,
            address=address,
            chain=chain,
            modules=allowed_modules(tier),
        )
        self._by_user[user_id] = job
        self._by_id[job.job_id] = job
        return job

    def get(self, job_id: str) -> ScanJob | None:
        """The job, or ``None`` when the id is unknown or pruned."""
        return self._by_id.get(job_id)

    def active(self, user_id: int) -> ScanJob | None:
        """The user's live job, if any."""
        job = self._by_user.get(user_id)
        return job if job is not None and job.status == RUNNING else None

    def advance(self, job_id: str, stage: str) -> ScanJob | None:
        """Move the progress line; a cancelled job ignores every later stage."""
        job = self._by_id.get(job_id)
        if job is None or job.cancelled:
            return job
        if stage in STAGES:
            job.stage = stage
        return job

    def cancel(self, job_id: str) -> bool:
        """Request cancellation. True when this call is what stopped the job."""
        job = self._by_id.get(job_id)
        if job is None or job.status != RUNNING:
            return False
        job.cancelled = True
        return True

    def finish(self, job_id: str) -> None:
        """Release the slot and the id map entry after the card is delivered."""
        job = self._by_id.pop(job_id, None)
        if job is not None and self._by_user.get(job.user_id) is job:
            self._by_user.pop(job.user_id, None)


def allowed_modules(tier: str) -> tuple[str, ...]:
    """Backend module tiers this subscription may run, cheapest first."""
    rank = rank_of(tier)
    return tuple(name for name in MODULE_TIERS if rank >= UNLOCK[name])


def blocked_modules(tier: str) -> tuple[str, ...]:
    """The modules this tier cannot run — named so the card can be honest."""
    return tuple(name for name in MODULE_TIERS if name not in allowed_modules(tier))


def render_progress(job: ScanJob) -> str:
    """One-line progress card the user can act on."""
    if job.status == CANCELLED:
        return (
            f"🛑 <b>Scan cancelled</b> — <code>{job.address}</code>\n"
            "No quota was consumed for the unfinished modules."
        )
    head = f"🔬 <b>Scanning</b> <code>{job.address}</code> ({job.chain})\n"
    body = f"{job.stage} · {int(job.progress * 100)}% · {len(job.modules)} modules"
    if job.stage == STAGES[-1]:
        return head + "✅ verdict ready"
    return head + body + "\n<i>/cancelscan to stop</i>"


#: The process-wide registry the handlers share.
REGISTRY = JobRegistry()
