"""Schedule-seam tests for the two daily-rug channels.

``DAILY_RUG_TIME`` and ``DAILY_RUG_UPDATE_TIME`` are read at IMPORT time into
module constants, so overriding one requires a module reload. This module owns
that reload discipline: every test rebakes the constant with the variable
explicitly set or explicitly unset, through the ``env`` fixture. Nothing is
patched, and no pytest-randomly ordering can leak an override into a default
assertion (the bug this file was split out to kill).
"""

from __future__ import annotations

import importlib

from rugmunchbot.channels.daily_rug import _config as morning
from rugmunchbot.channels.daily_rug_update import _config as midday


def test_morning_default(env) -> None:
    env.unset("RMI_DAILY_RUG_TIME")
    importlib.reload(morning)
    t = morning.daily_rug_run_time()
    assert (t.hour, t.minute) == (7, 30)
    assert t.tzinfo is not None


def test_morning_env_override(env) -> None:
    env["RMI_DAILY_RUG_TIME"] = "06:45"
    importlib.reload(morning)
    try:
        t = morning.daily_rug_run_time()
        assert (t.hour, t.minute) == (6, 45)
    finally:
        env.unset("RMI_DAILY_RUG_TIME")
        importlib.reload(morning)


def test_midday_default(env) -> None:
    env.unset("RMI_DAILY_RUG_UPDATE_TIME")
    importlib.reload(midday)
    t = midday.rug_update_run_time()
    assert (t.hour, t.minute) == (12, 30)
    assert t.tzinfo is not None


def test_midday_env_override(env) -> None:
    env["RMI_DAILY_RUG_UPDATE_TIME"] = "13:15"
    importlib.reload(midday)
    try:
        t = midday.rug_update_run_time()
        assert (t.hour, t.minute) == (13, 15)
    finally:
        env.unset("RMI_DAILY_RUG_UPDATE_TIME")
        importlib.reload(midday)
