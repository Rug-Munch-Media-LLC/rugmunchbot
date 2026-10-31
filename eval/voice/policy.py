"""Voice calibration seam: the engine is public, the calibration is not.

The seven sub-scores are fixed (``rules.SUBSCORE_NAMES``); their relative
importance is fleet calibration and stays outside this repo. A checkout
injects the real calibration with ``RMB_VOICE_WEIGHTS_FILE`` - a JSON object
whose keys are exactly the sub-score names and whose positive values sum to
1.0. Without it the eval runs on ``example_weights()``: uniform 1/7, a
shape-only default that is deliberately not the fleet calibration (a contract
test pins it so it cannot quietly grow into a reduced copy).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import NamedTuple

import rules

#: Environment variable naming the private calibration file (JSON object).
ENV_FILE = "RMB_VOICE_WEIGHTS_FILE"

#: A unit-summing calibration must land in this window - the same window the
#: methodology gate treats as a calibration, so the contract is one number.
SUM_LOW, SUM_HIGH = 0.9, 1.1

EXAMPLE_SOURCE = "example: uniform 1/7 (shape-only default)"


class Calibration(NamedTuple):
    """The resolved calibration plus its source, surfaced in the eval report."""

    weights: dict[str, float]
    source: str


def example_weights() -> dict[str, float]:
    """Uniform shape-only default: every sub-score carries equal 1/7 weight."""
    return dict.fromkeys(rules.SUBSCORE_NAMES, 1.0 / len(rules.SUBSCORE_NAMES))


def _validated(raw: dict, source: str) -> Calibration:
    """Accept only a complete, positive, unit-summing calibration - fail loud."""
    if set(raw) != set(rules.SUBSCORE_NAMES):
        raise ValueError(
            f"{source}: calibration must define exactly {sorted(rules.SUBSCORE_NAMES)}"
        )
    weights = {name: float(value) for name, value in raw.items()}
    bad = [name for name, value in weights.items() if not value > 0.0]
    if bad:
        raise ValueError(f"{source}: calibration values must be positive, got <= 0 for {bad}")
    mass = sum(weights.values())
    if not SUM_LOW <= mass <= SUM_HIGH:
        raise ValueError(f"{source}: calibration must sum to ~1.0, got {mass:.4f}")
    return Calibration(weights=weights, source=source)


def load_weights() -> Calibration:
    """Resolve the calibration: RMB_VOICE_WEIGHTS_FILE, else the uniform default."""
    path = os.environ.get(ENV_FILE, "")
    if not path:
        return Calibration(weights=example_weights(), source=EXAMPLE_SOURCE)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: calibration file must hold a JSON object")
    return _validated(data, path)
