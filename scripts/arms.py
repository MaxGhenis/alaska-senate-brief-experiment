"""Analyse the arms: each run's measures, each arm's means and the registered effects.

    .venv/bin/python scripts/arms.py [--draws 100000] [--seed 0] [--out outputs/arms]

Reads the runs `i86 check` and `i86 audit` accepted (cli.accepted_runs, against each run's own arm's
brief), with each run's family and arm from its meta.json, and writes:

- per_run.csv: every run's answer at every range, and its contrast, jump, abs_jump and slope, per
  question;
- arm_means.csv: each arm's family-balanced mean of each measure, with its standard error and df;
- effects.csv: each block's main effect and their interaction on each measure, with standard error,
  Welch-Satterthwaite df, a 95% t interval and a randomization p-value;
- summary.md: the three registered primary outcomes first, then the tables above in brief.

See src/i86/arms.py for the definitions. These are model forecasts under stated scenarios, not
observations.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from scipy import stats  # noqa: E402

from i86 import arms, cli, graph as graph_mod, runners  # noqa: E402


def interval(e: arms.Estimate) -> tuple[float, float]:
    if not (math.isfinite(e.se) and math.isfinite(e.df)) or e.df <= 0:
        return math.nan, math.nan
    q = stats.t.ppf(0.975, e.df)
    return e.est - q * e.se, e.est + q * e.se


def collect(g, runs: dict) -> list[dict]:
    """One row per run: label, family, arm, the run's measures and its answers by range."""
    out = []
    for label, fc in sorted(runs.items()):
        meta = runners.read_meta(runners.RUNS / label)
        family, arm = meta.get("family"), meta.get("arm")
        if family not in arms.FAMILIES or arm not in arms.ARMS:
            raise SystemExit(f"{label}: meta.json lacks a family and arm from design/assignment.json")
        out.append({"label": label, "family": family, "arm": arm, "measures": arms.run_measures(g, fc),
                    "answers": {n.id: {g.label(c): arms.answer(n, fc.values[n.id][g.label(c)]) for c in g.cells}
                                for n in arms.ranged_nodes(g)}})
    return out


def rows_for(runs: list[dict], nodes, measure: str) -> list[tuple[str, str, float]]:
    return [(r["family"], r["arm"], arms.index(r["measures"], nodes, measure)) for r in runs]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--draws", type=int, default=100_000, help="re-randomizations per registered primary")
    ap.add_argument("--secondary-draws", type=int, default=20_000, help="re-randomizations per other effect")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=ROOT / "outputs" / "arms")
    ap.add_argument("--accept-prior", action="append", default=[], metavar="LABEL")
    ap.add_argument("--accept-suspect", action="append", default=[], metavar="LABEL")
    ap.add_argument("--accept-all-flags", action="store_true",
                    help="the registered sensitivity check: accept every suspect call and prior-context flag "
                         "(a violation, a failed audit or a stale brief still excludes a run)")
    args = ap.parse_args(argv)
    g = graph_mod.load()
    if args.accept_all_flags:
        every = {d.name for d in runners.run_dirs()}
        args.accept_prior, args.accept_suspect = sorted(every), sorted(every)
    runs = cli.accepted_runs(g, cli._prompt_sha(), accept_prior=set(args.accept_prior),
                             accept_suspect=set(args.accept_suspect))
    if not runs:
        raise SystemExit("no accepted runs: collect, check and audit first")
    data = collect(g, runs)
    args.out.mkdir(parents=True, exist_ok=True)
    nodes = [n.id for n in arms.ranged_nodes(g)]

    with open(args.out / "per_run.csv", "w", newline="") as f:
        labels = [g.label(c) for c in g.cells]
        w = csv.writer(f)
        w.writerow(["run", "family", "arm", "question", *arms.MEASURES, *labels])
        for r in data:
            for n in nodes:
                w.writerow([r["label"], r["family"], r["arm"], n, *(r["measures"][n][m] for m in arms.MEASURES),
                            *(r["answers"][n][lab] for lab in labels)])

    with open(args.out / "arm_means.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question", "measure", "arm", "mean", "se", "df", "n_opus", "n_sol"])
        for n in nodes:
            for m in arms.MEASURES:
                rows = rows_for(data, (n,), m)
                for a in arms.ARMS:
                    e = arms.arm_mean(rows, a)
                    w.writerow([n, m, a, e.est, e.se, e.df, e.n.get("opus", 0), e.n.get("sol", 0)])

    primaries = []
    for name, qs, m, contrast in arms.PRIMARY:
        rows = rows_for(data, qs, m)
        e = arms.effect(rows, arms.CONTRASTS[contrast])
        p = arms.randomization_p(rows, arms.CONTRASTS[contrast], draws=args.draws, seed=args.seed)
        primaries.append((name, "+".join(qs), m, contrast, e, interval(e), p))
    adjusted = arms.holm({name: p for name, *_, p in primaries})

    with open(args.out / "effects.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["outcome", "questions", "measure", "contrast", "estimate", "se", "df", "ci_lo", "ci_hi",
                    "randomization_p", "holm_p", "registered_primary"])
        for name, qs, m, contrast, e, (lo, hi), p in primaries:
            w.writerow([name, qs, m, contrast, e.est, e.se, e.df, lo, hi, p, adjusted[name], True])
        for n in nodes:
            for m in arms.MEASURES:
                rows = rows_for(data, (n,), m)
                for contrast, weights in arms.CONTRASTS.items():
                    e = arms.effect(rows, weights)
                    lo, hi = interval(e)
                    p = arms.randomization_p(rows, weights, draws=args.secondary_draws, seed=args.seed)
                    w.writerow([n, n, m, contrast, e.est, e.se, e.df, lo, hi, p, "", False])

    counts = {(fam, a): sum(1 for r in data if r["family"] == fam and r["arm"] == a)
              for fam in arms.FAMILIES for a in arms.ARMS}
    flags = " Every suspect call and prior-context flag accepted (the sensitivity check)." if args.accept_all_flags else ""
    lines = ["# What the briefs' blocks change", "",
             "Model forecasts under stated scenarios, not observations." + flags + " Accepted runs by family and arm: "
             + ", ".join(f"{fam} {a}: {k}" for (fam, a), k in counts.items()) + ".", "",
             "## Registered primary outcomes", "",
             "| outcome | questions | measure | block | estimate | 95% t interval | randomization p | Holm p |",
             "|---|---|---|---|---|---|---|---|"]
    for name, qs, m, contrast, e, (lo, hi), p in primaries:
        lines.append(f"| {name} | {qs} | {m} | {contrast} | {e.est:.4g} | [{lo:.4g}, {hi:.4g}] | {p:.4g} "
                     f"| {adjusted[name]:.4g} |")
    lines += ["", "## By family (exploratory)", "",
              "| outcome | Claude Opus 5.5 | GPT-6.1 Sol |", "|---|---|---|"]
    with open(args.out / "primary_by_family.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["outcome", "family", "estimate", "se", "df", "ci_lo", "ci_hi", "n"])
        for name, qs, m, contrast, *_ in primaries:
            rows = rows_for(data, qs.split("+"), m)
            cells_ = []
            for fam in arms.FAMILIES:
                e = arms.family_effect(rows, arms.CONTRASTS[contrast], fam)
                lo, hi = interval(e)
                w.writerow([name, fam, e.est, e.se, e.df, lo, hi, sum(e.n.values())])
                cells_.append(f"{e.est:.4g} [{lo:.4g}, {hi:.4g}]")
            lines.append(f"| {name} | {cells_[0]} | {cells_[1]} |")
    lines += ["", "Every other effect is in effects.csv (exploratory); arm means are in arm_means.csv.", ""]
    (args.out / "summary.md").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
