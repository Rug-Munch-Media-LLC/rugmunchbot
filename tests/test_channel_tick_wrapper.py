"""Guard: every registered channel job is wrapped in ``_tick_wrapper``.

Rationale (2026-10-17 cadence audit): six channels read as "silent" because four
of them (``channel_dislocation``, ``channel_whale_alerts``,
``channel_social_spikes``, ``channel_lp_expiry``) were registered as bare inline
tick functions. Only ``_tick_wrapper`` records ``last_run``, so those jobs had no
telemetry at all and were indistinguishable from dead channels.
"""

from __future__ import annotations

import ast
from pathlib import Path

REGISTER = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "rugmunchbot"
    / "channels"
    / "scheduler"
    / "_register.py"
)

# Jobs that legitimately bypass ``_tick_wrapper``. ``channel_state_load`` runs
# ONCE at boot to hydrate scheduler state and is the thing that makes last_run
# durable — wrapping it in a state-writing wrapper would be circular. Add a name
# here only with a one-line reason; content channels must never join this set.
EXPLICITLY_DISABLED: frozenset[str] = frozenset({"channel_state_load"})


def test_every_registered_channel_job_is_tick_wrapped() -> None:
    """Every ``job_queue.run_*`` call must pass ``_tick_wrapper(...)`` as its callback."""
    tree = ast.parse(REGISTER.read_text(encoding="utf-8"))

    registered: dict[str, str] = {}  # job name -> callback expression kind
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr.startswith("run_")
            and isinstance(func.value, ast.Name)
            and func.value.id == "job_queue"
        ):
            continue

        job_name = ""
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                job_name = str(kw.value.value)

        first = node.args[0]
        wrapped = (
            isinstance(first, ast.Call)
            and isinstance(first.func, ast.Name)
            and first.func.id == "_tick_wrapper"
        )
        kind = "wrapped" if wrapped else "bare"
        if wrapped:
            for inner in first.args:
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    job_name = inner.value
                    break
        registered[job_name or "<unnamed>"] = kind

    assert registered, "no channel jobs found — the AST scan or the file moved"

    bare = sorted(n for n, k in registered.items() if k == "bare")
    offenders = [n for n in bare if n not in EXPLICITLY_DISABLED]
    assert not offenders, (
        "channel jobs registered without _tick_wrapper (no last_run telemetry): "
        f"{offenders}. Wrap them, or record them as intentionally disabled."
    )

    # The four channels the cadence audit flagged as telemetry-blind.
    for name in (
        "channel_dislocation",
        "channel_whale_alerts",
        "channel_social_spikes",
        "channel_lp_expiry",
    ):
        assert registered.get(name) == "wrapped", f"{name} is not tick-wrapped"

    # Gated channels must explain their absence rather than vanish silently.
    source = REGISTER.read_text(encoding="utf-8")
    for name in ("channel_personal", "channel_updates"):
        assert registered.get(name) == "wrapped", f"{name} is not tick-wrapped"
        assert "channels:disabled" in source
