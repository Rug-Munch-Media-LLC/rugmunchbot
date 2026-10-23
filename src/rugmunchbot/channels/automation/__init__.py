"""Unified content-automation lane (smart-money, liquidation, odds, calls).

Four event-driven posts, one gate: every candidate carries a stable event
key, evidence/source/timestamp/risk in the body, dedupe (memory + durable),
and an hourly rate limit. Dry-run is the default.
"""

from __future__ import annotations

from .gate import LaneGate
from .lane_call import fetch_calls
from .plan import PostPlan
from .register import register_content_jobs
from .render_call import render_call
from .runner import LANE_NAMES, LANES, run_lane
from .select_call import select_calls
from .select_liquidation import select_liquidations
from .select_move import select_moves
from .select_odds import select_odds

__all__ = [
    "LANES",
    "LANE_NAMES",
    "LaneGate",
    "PostPlan",
    "fetch_calls",
    "register_content_jobs",
    "render_call",
    "run_lane",
    "select_calls",
    "select_liquidations",
    "select_moves",
    "select_odds",
]
