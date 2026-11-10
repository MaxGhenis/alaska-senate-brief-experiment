"""Recompute the registered primaries from the sealed answers without the package. Exploratory: a
check on scripts/arms.py, not part of the registered analysis.

    .venv/bin/python scripts/recompute.py [--analysis outputs/arms] [--runs runs]

This script imports nothing from `src/i86`. It takes the list of pooled runs from an analysis's
per_run.csv, then reads each run's family and arm from its meta.json and its answers from its
response.json, and follows README's "Registered analysis" by hand:

- a run's answer at a range: the probability for a yes/no question, the median for a quantity;
- `jump`: the gap at 50% between two least-squares lines, Peltola's minus Sullivan's, each through
  three closed ranges at their midpoints (48.75%, 49.25% and 49.75%; 50.25%, 50.75% and 51.25%);
- P1 and P2: the absolute jump of `bm2_yes_share` and of `gov_dem_share`; E1: the mean absolute jump
  over `recount`, `contest`, `court_order`, `winner_sworn` and `sullivan_post`;
- an arm's mean: the mean of the two models' means; a block's main effect: the two arms with the block
  less the two without it;
- the standard error, from each model's pooled variance about its arms' means, and Welch-Satterthwaite
  degrees of freedom.

It prints each primary's estimate, standard error and degrees of freedom beside the analysis's
effects.csv, and exits 1 if any differs by more than rounding. These are model forecasts under stated
scenarios, not observations.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SULLIVAN = ("dem_48.5_49.0", "dem_49.0_49.5", "dem_49.5_50.0")  # midpoints 48.75, 49.25, 49.75
PELTOLA = ("dem_50.0_50.5", "dem_50.5_51.0", "dem_51.0_51.5")   # midpoints 50.25, 50.75, 51.25
ARMS = ("00", "01", "10", "11")  # first digit: the estimand block; second: the placebo block
PRIMARIES = (
    ("P1 coached placebo", ("bm2_yes_share",), 1),
    ("P2 uncoached placebo", ("gov_dem_share",), 1),
    ("E1 effect index", ("recount", "contest", "court_order", "winner_sworn", "sullivan_post"), 0),
)


def line_at_50(xs: tuple[float, ...], ys: list[float]) -> float:
    """The least-squares line through (xs, ys), read at 50."""
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return my + slope * (50.0 - mx)


def answer(forecast: dict) -> float:
    """A run's answer at one range: the probability for a yes/no question, the median for a quantity."""
    return float(forecast["p"] if "p" in forecast else forecast["p50"])


def abs_jump(forecasts: dict) -> float:
    left = line_at_50((48.75, 49.25, 49.75), [answer(forecasts[k]) for k in SULLIVAN])
    right = line_at_50((50.25, 50.75, 51.25), [answer(forecasts[k]) for k in PELTOLA])
    return abs(right - left)


def main_effect(rows: list[tuple[str, str, float]], digit: int) -> tuple[float, float, float]:
    """(estimate, standard error, Welch-Satterthwaite df) of a block's main effect: `digit` picks the
    block (0 the estimand block, 1 the placebo block), and each model counts equally."""
    families = sorted({fam for fam, _, _ in rows})
    estimate, variance, parts = 0.0, 0.0, []
    for fam in families:
        cells = {arm: [v for f, a, v in rows if f == fam and a == arm] for arm in ARMS}
        means = {arm: sum(vs) / len(vs) for arm, vs in cells.items()}
        runs = sum(len(vs) for vs in cells.values())
        pooled = sum((v - means[arm]) ** 2 for arm, vs in cells.items() for v in vs) / (runs - len(ARMS))
        estimate += sum((0.5 if arm[digit] == "1" else -0.5) * means[arm] for arm in ARMS) / len(families)
        part = pooled * sum(0.25 / len(vs) for vs in cells.values()) / len(families) ** 2
        variance += part
        parts.append((part, runs - len(ARMS)))
    df = variance ** 2 / sum(part ** 2 / d for part, d in parts)
    return estimate, math.sqrt(variance), df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--analysis", type=Path, default=ROOT / "outputs" / "arms")
    ap.add_argument("--runs", type=Path, default=ROOT / "runs")
    a = ap.parse_args(argv)
    with open(a.analysis / "per_run.csv", newline="") as f:
        pooled = sorted({r["run"] for r in csv.DictReader(f)})
    with open(a.analysis / "effects.csv", newline="") as f:
        registered = {r["outcome"]: r for r in csv.DictReader(f) if r.get("registered_primary") == "True"}
    runs = []
    for label in pooled:
        meta = json.loads((a.runs / label / "meta.json").read_text())
        nodes = json.loads((a.runs / label / "response.json").read_text())["nodes"]
        runs.append((meta["family"], meta["arm"], nodes))
    worst = 0.0
    print(f"{len(pooled)} runs from {a.analysis}")
    for name, questions, digit in PRIMARIES:
        rows = [(fam, arm, sum(abs_jump(nodes[q]["forecasts"]) for q in questions) / len(questions))
                for fam, arm, nodes in runs]
        got = main_effect(rows, digit)
        want = tuple(float(registered[name][k]) for k in ("estimate", "se", "df"))
        worst = max(worst, *(abs(g - w) / max(1.0, abs(w)) for g, w in zip(got, want)))
        print(f"{name}: estimate {got[0]:.9f} (effects.csv {want[0]:.9f}), standard error {got[1]:.9f} "
              f"({want[1]:.9f}), df {got[2]:.6f} ({want[2]:.6f})")
    print("match" if worst < 1e-9 else f"MISMATCH: largest relative difference {worst:.3g}")
    return 0 if worst < 1e-9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
