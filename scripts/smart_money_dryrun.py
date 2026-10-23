#!/usr/bin/env python3
"""Dry-run proof for the smart-money / whale channel surface.

Renders one post per generator, prints them, and runs each through the
publisher's gate in dry-run mode (``bot=None``) — Telegram is never called.
Provenance is printed per post: ``LIVE`` (backend feed) or ``FIXTURE``
(synthetic-shape row used only when the live feed returns no rows).

    python3 scripts/smart_money_dryrun.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rugmunchbot.smart_money import (
    Feed,
    event_key,
    fetch_hl,
    fetch_moves,
    fetch_predictions,
    moves as moves_mod,
    publish,
    render_hl,
    render_move,
    render_prediction,
)

ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC = ROOT / "tests/fixtures/smart_money/smart_money_moves_synthetic.json"


def _synthetic_move() -> dict:
    row = json.loads(SYNTHETIC.read_text())["smart_money_wallets"][0]
    return moves_mod._normalise(row, "solana") or {}


async def main() -> int:
    rendered: list[tuple[str, str, str, str]] = []

    feed = await fetch_moves(limit=5)
    if feed.ok:
        rendered.append(
            (
                "moves",
                str(render_move(feed, feed.items[0])),
                event_key("moves", feed.items[0]),
                f"LIVE · {feed.source}",
            )
        )
    else:
        row = _synthetic_move()
        fixture_feed = Feed(kind="moves", source="fixture.synthetic_shape")
        rendered.append(
            (
                "moves",
                str(render_move(fixture_feed, row)),
                event_key("moves", row),
                "FIXTURE (synthetic-shape) · live feeds returned no rows",
            )
        )

    hl_feed = await fetch_hl(limit=6)
    if hl_feed.ok:
        rendered.append(
            (
                "hl",
                str(render_hl(hl_feed, hl_feed.items[0])),
                event_key("hl", hl_feed.items[0]),
                f"LIVE · {hl_feed.source}",
            )
        )

    pred_feed = await fetch_predictions(limit=4)
    if pred_feed.ok:
        rendered.append(
            (
                "predictions",
                str(render_prediction(pred_feed, pred_feed.items[0])),
                event_key("predictions", pred_feed.items[0]),
                f"LIVE · {pred_feed.source}",
            )
        )

    for i, (kind, body, key, provenance) in enumerate(rendered, 1):
        print(f"\n{'=' * 70}\nPOST {i}/{len(rendered)} · {kind} · {provenance}\n{'=' * 70}\n{body}")
        envelope = await publish(body, key=key, bot=None)
        print(f"-- publish envelope: {json.dumps(envelope, sort_keys=True)}")
    missing = 3 - len(rendered)
    if missing:
        print(f"\n[INCOMPLETE] {missing} generator(s) had no live data and no fixture fallback.")
    return 0 if len(rendered) == 3 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
