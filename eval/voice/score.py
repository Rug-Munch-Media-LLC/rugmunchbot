"""Deterministic voice scorer: does the bot sound right?

Rules, not vibes. Seven weighted checks produce a 0-1 score per sample in
``eval/voice/gold.json``. CLI exits non-zero when the aggregate is below
``--min``, so it is a CI gate:

    python3 eval/voice/score.py --min 0.75

The calibration (the weights behind "weighted") is resolved by
``policy.load_weights()``: ``RMB_VOICE_WEIGHTS_FILE`` when set, otherwise the
uniform shape-only example. The report names the source either way.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics
import policy
import rules

GOLD = Path(__file__).resolve().parent / "gold.json"


def score_sample(sample: dict, weights: dict[str, float] | None = None) -> dict:
    """Grade one sample's ``current`` text against every weighted rule."""
    w = policy.example_weights() if weights is None else weights
    m = metrics.measure(sample["current"])
    notes: list[str] = []

    hits = m["banned"]
    banned = 1.0 if not hits else (0.6 if len(hits) == 1 else 0.0)
    if hits:
        notes.append(f"banned={hits[0]!r}")

    clich = m["cliches"]
    if not clich:
        cliche = 1.0
    else:
        density = len(clich) / max(1.0, m["words"] / 100.0)
        cliche = 0.7 if density <= 0.5 else (0.4 if density <= 1.5 else 0.0)
        notes.append(f"cliche={clich[0]!r} (density {density:.2f})")

    spread = m["spread"]
    if m["sentences"] < 2:
        spread_s = 0.5
    elif 2.0 <= spread <= 12.0:
        spread_s = 1.0
    elif spread < 2.0:
        spread_s = 0.5 * (spread / 2.0)
        notes.append(f"spread=uniform (sd {spread})")
    else:
        spread_s = max(0.4, 1.0 - (spread - 12.0) / 20.0)
        notes.append(f"spread=ragged (sd {spread})")

    emo = m["emoji"]
    emoji_s = 1.0 if emo <= 1 else (0.85 if emo == 2 else (0.6 if emo == 3 else 0.3))
    if emo > 1:
        notes.append(f"emoji={emo}/msg")

    hedge_s = max(0.0, 1.0 - 0.35 * len(m["hedges"]))
    if m["hedges"]:
        notes.append(f"hedge={m['hedges'][0]!r}")

    for leg in ("headline", "fact", "cta"):
        if not m[leg]:
            notes.append(f"structure=missing {leg}")
    struct = (int(m["headline"]) + int(m["fact"]) + int(m["cta"])) / 3.0

    if not sample.get("serious"):
        reg = 1.0
    elif m["jokes"]:
        reg = 0.0
        notes.append(f"register=joke in {sample['category']}: {m['jokes'][0]!r}")
    elif m["exclaim"] > 2:
        reg = 0.5
        notes.append(f"register={m['exclaim']} exclamations")
    elif emo > 1:
        reg = 0.7
        notes.append("register=emoji stack on serious message")
    else:
        reg = 1.0

    subs = {
        "banned": banned,
        "cliche": cliche,
        "spread": spread_s,
        "emoji": emoji_s,
        "hedge": hedge_s,
        "structure": struct,
        "register": reg,
    }
    total = sum(w[k] * v for k, v in subs.items())
    if reg == 0.0:
        # Hard veto: a joke in a loss/security/error/moderation message is a
        # defect no amount of good structure can offset.
        total = 0.0
    return {
        "id": sample["id"],
        "category": sample["category"],
        "serious": bool(sample.get("serious")),
        "score": round(total, 4),
        "subs": subs,
        "notes": notes,
    }


def main(argv: list[str] | None = None) -> int:
    """Score the gold set, print the report, gate on ``--min``."""
    ap = argparse.ArgumentParser(description="rugmunchbot voice eval")
    ap.add_argument("--min", type=float, default=rules.TARGET_MIN)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args(argv)

    cal = policy.load_weights()
    samples = json.loads(GOLD.read_text(encoding="utf-8"))
    results = [score_sample(s, cal.weights) for s in samples]
    aggregate = sum(r["score"] for r in results) / len(results)
    worst = sorted(results, key=lambda r: r["score"])[: args.top]

    if args.json:
        print(
            json.dumps(
                {
                    "aggregate": round(aggregate, 4),
                    "n": len(results),
                    "min": args.min,
                    "calibration": cal.source,
                    "worst": worst,
                    "results": results,
                },
                indent=2,
            )
        )
    else:
        print(
            f"voice eval · {len(results)} samples · aggregate {aggregate:.4f} "
            f"· gate {args.min:.2f} · calibration: {cal.source} · "
            f"{'PASS' if aggregate >= args.min else 'FAIL'}"
        )
        print(f"\nworst {len(worst)}:")
        for r in worst:
            print(f"  {r['score']:.3f}  {r['id']:<26} {'; '.join(r['notes'][:2])}")

    return 0 if aggregate >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
