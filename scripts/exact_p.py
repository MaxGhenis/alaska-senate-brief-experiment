"""Exact randomization p-values for the three registered primaries. Exploratory: a check on the
registered Monte Carlo p-values, not part of the registered analysis.

    .venv/bin/python scripts/exact_p.py [--per-run outputs/arms/per_run.csv] [--out outputs/exact-p]

The registered test re-assigns arms among each model's runs 100,000 times, keeping each arm's count,
and reports the share of those assignments, the observed one counted in, whose effect lies at least as
far from zero as the observed effect (README, "Estimates"). This script counts every assignment
instead. Eleven runs in arms of 3, 3, 3 and 2 allow 92,400 of them, and ten runs in arms of 3, 2, 3
and 2 allow 25,200. An estimate averages the models' contrasts, so the script lists each model's
contrast under each of its assignments, sorts one model's list and, for each value in the other's,
counts the pairs whose mean lies at least as far from zero as the observed effect. It keeps the
registered tolerance: 1e-9 times the larger of 1 and the observed effect's size.

It reads an analysis's per-run table and the effects.csv beside it, stops if its estimate differs
from that file's, and writes exact_p.csv: each primary's estimate, each model's count of assignments,
the exact p-value beside the Monte Carlo one, and Holm's correction of each. These are model forecasts
under stated scenarios, not observations.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from i86 import arms  # noqa: E402

BLOCK = {"placebo": arms.MAIN_P, "estimand": arms.MAIN_E}


def primary_rows(per_run_csv: Path) -> dict[str, list[tuple[str, str, float]]]:
    """{primary: [(family, arm, value)]}: each run's value on each registered primary, the mean of its
    measure over the primary's questions."""
    table: dict[tuple[str, str, str], dict[str, dict[str, str]]] = {}
    with open(per_run_csv, newline="") as f:
        for r in csv.DictReader(f):
            table.setdefault((r["run"], r["family"], r["arm"]), {})[r["question"]] = r
    return {name: [(fam, arm, statistics.fmean(float(q[n][measure]) for n in nodes))
                   for (_, fam, arm), q in sorted(table.items())]
            for name, nodes, measure, _ in arms.PRIMARY}


def assignments(counts: tuple[int, ...]) -> np.ndarray:
    """Every way to give arms 0, 1, ... to sum(counts) runs so that arm j gets counts[j] of them: one
    row per assignment, one column per run."""
    n, rows, left = sum(counts), [], list(counts)

    def fill(prefix: tuple[int, ...]) -> None:
        if len(prefix) == n:
            rows.append(prefix)
            return
        for arm, free in enumerate(left):
            if free:
                left[arm] -= 1
                fill(prefix + (arm,))
                left[arm] += 1

    fill(())
    return np.array(rows, dtype=np.int8).reshape(len(rows), n)


def family_contrasts(values: list[float], arm_of: list[str], contrast: dict[str, float]) -> tuple[np.ndarray, float]:
    """One model's contrast (its arms' means, weighted) under every assignment that keeps each arm's
    count, and under the observed assignment."""
    y = np.array(values, dtype=float)
    observed = np.array([arms.ARMS.index(a) for a in arm_of])
    counts = tuple(int((observed == j).sum()) for j in range(len(arms.ARMS)))
    every = assignments(counts)
    under = np.zeros(len(every))
    for j, arm in enumerate(arms.ARMS):
        under += contrast[arm] * np.where(every == j, y, 0.0).sum(axis=1) / counts[j]
    return under, float(sum(contrast[arm] * y[observed == j].mean() for j, arm in enumerate(arms.ARMS)))


def exact_p(rows, contrast: dict[str, float]) -> tuple[float, float, dict[str, int]]:
    """(estimate, exact two-sided p-value, {family: its count of assignments}) for one effect, over the
    families with a run in every arm. Handles one or two such families."""
    rows = [(f, a, float(v)) for f, a, v in rows]
    fams = arms.complete_families(arms.cells(rows))
    if not 1 <= len(fams) <= 2:
        raise ValueError(f"exact_p handles one or two complete families, not {len(fams)}")
    parts = [family_contrasts([v for f, _, v in rows if f == fam], [a for f, a, _ in rows if f == fam], contrast)
             for fam in fams]
    estimate = sum(obs for _, obs in parts) / len(fams)
    threshold = abs(estimate) - 1e-9 * max(1.0, abs(estimate))
    first = parts[0][0] / len(fams)
    if len(fams) == 1:
        hits, total = int((np.abs(first) >= threshold).sum()), len(first)
    else:
        second = np.sort(parts[1][0] / len(fams))
        above = len(second) - np.searchsorted(second, threshold - first, side="left")   # first + second >= threshold
        below = np.searchsorted(second, -threshold - first, side="right")              # first + second <= -threshold
        both = np.minimum(above + below, len(second)) if threshold <= 0 else above + below
        hits, total = int(both.sum()), len(first) * len(second)
    return estimate, hits / total, {fam: len(part[0]) for fam, part in zip(fams, parts)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--per-run", type=Path, default=ROOT / "outputs" / "arms" / "per_run.csv")
    ap.add_argument("--effects", type=Path, default=None, help="default: effects.csv beside --per-run")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs" / "exact-p")
    a = ap.parse_args(argv)
    with open(a.effects or a.per_run.with_name("effects.csv"), newline="") as f:
        registered = {r["outcome"]: r for r in csv.DictReader(f) if r.get("registered_primary") == "True"}
    found = {}
    for (name, _, _, block), rows in zip(arms.PRIMARY, primary_rows(a.per_run).values()):
        estimate, p, counts = exact_p(rows, BLOCK[block])
        if not math.isclose(estimate, float(registered[name]["estimate"]), rel_tol=1e-9, abs_tol=1e-12):
            raise SystemExit(f"{name}: per_run.csv gives {estimate}; effects.csv says {registered[name]['estimate']}")
        found[name] = (estimate, p, counts)
    holm_exact = arms.holm({name: p for name, (_, p, _) in found.items()})
    holm_monte_carlo = arms.holm({name: float(registered[name]["randomization_p"]) for name in found})
    a.out.mkdir(parents=True, exist_ok=True)
    with open(a.out / "exact_p.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["primary", "estimate", "assignments", "exact_p", "monte_carlo_p", "holm_exact", "holm_monte_carlo"])
        for name, (estimate, p, counts) in found.items():
            w.writerow([name, estimate, "; ".join(f"{fam} {n}" for fam, n in counts.items()), p,
                        registered[name]["randomization_p"], holm_exact[name], holm_monte_carlo[name]])
            print(f"{name}: estimate {estimate:.6g}, exact p {p:.4f} (Monte Carlo {float(registered[name]['randomization_p']):.4f}), "
                  f"Holm {holm_exact[name]:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
