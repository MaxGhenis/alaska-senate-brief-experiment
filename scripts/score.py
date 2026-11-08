"""Score every accepted run against outcomes/outcomes.yaml, by the rule each brief states.

    .venv/bin/python scripts/score.py [--outcomes outcomes/outcomes.yaml] [--out outputs/scores]

Each run's forecast for the range the certified Senate result falls in is scored (src/i86/score.py);
the other seven ranges' forecasts are void. Questions not yet in the outcomes file are left out, so
the script can run as questions resolve, through mid-2027. Writes:

- per_run.csv: each run's score on each resolved question, with the rule and the realized range;
- by_arm.csv: each arm's family-balanced mean score per question, with its standard error;
- effects.csv: each block's main effect and their interaction on each question's score, with a
  randomization p-value (exploratory: the study's registered tests are in scripts/arms.py);
- summary.md.

Lower scores are better. A score is a forecast's accuracy on one outcome; with 24 runs and one
election, it says little about any one forecaster.
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

from i86 import arms, cli, graph as graph_mod, runners, score  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--outcomes", type=Path, default=score.OUTCOMES_PATH)
    ap.add_argument("--out", type=Path, default=ROOT / "outputs" / "scores")
    ap.add_argument("--draws", type=int, default=20_000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--accept-prior", action="append", default=[], metavar="LABEL")
    ap.add_argument("--accept-suspect", action="append", default=[], metavar="LABEL")
    args = ap.parse_args(argv)
    g = graph_mod.load()
    outcomes = score.load_outcomes(g, args.outcomes)
    runs = cli.accepted_runs(g, cli._prompt_sha(), accept_prior=set(args.accept_prior),
                             accept_suspect=set(args.accept_suspect))
    if not runs:
        raise SystemExit("no accepted runs")
    rows = []
    for label, fc in sorted(runs.items()):
        meta = runners.read_meta(runners.RUNS / label)
        if meta.get("family") not in arms.FAMILIES or meta.get("arm") not in arms.ARMS:
            raise SystemExit(f"{label}: meta.json lacks a family and arm from design/assignment.json")
        for r in score.score_run(g, fc, outcomes):
            rows.append({"run": label, "family": meta["family"], "arm": meta["arm"], **r})
    args.out.mkdir(parents=True, exist_ok=True)
    senate = outcomes.get("dem_share")
    if not rows:
        why = ("the Senate result is not in the outcomes file yet" if senate is None
               else f"the Senate result is void ({senate.void})" if senate.value is None
               else "no question has resolved")
        (args.out / "summary.md").write_text(f"# Scores\n\nNothing scored: {why}.\n")
        print(f"nothing scored: {why}")
        return 0
    with open(args.out / "per_run.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["run", "family", "arm", "question", "rule", "range", "score"])
        w.writeheader()
        w.writerows(rows)
    questions = list(dict.fromkeys(r["question"] for r in rows))
    by_q = {q: [(r["family"], r["arm"], r["score"]) for r in rows if r["question"] == q] for q in questions}
    with open(args.out / "by_arm.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question", "rule", "arm", "mean", "se", "n_opus", "n_sol"])
        for q in questions:
            rule = next(r["rule"] for r in rows if r["question"] == q)
            for a in arms.ARMS:
                e = arms.arm_mean(by_q[q], a)
                w.writerow([q, rule, a, e.est, e.se, e.n.get("opus", 0), e.n.get("sol", 0)])
    lines = ["# Scores", "",
             f"The Senate result fell in `{rows[0]['range']}` ({100 * senate.value:.3f}% for Peltola; "
             f"{senate.source}, as of {senate.as_of}). Lower is better. Effects are exploratory.", "",
             "| question | rule | mean, all runs | estimand block | placebo block |", "|---|---|---|---|---|"]
    with open(args.out / "effects.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question", "contrast", "estimate", "se", "df", "randomization_p"])
        for q in questions:
            cells = {}
            for name, weights in arms.CONTRASTS.items():
                e = arms.effect(by_q[q], weights)
                p = arms.randomization_p(by_q[q], weights, draws=args.draws, seed=args.seed)
                w.writerow([q, name, e.est, e.se, e.df, p])
                cells[name] = f"{e.est:+.4g} (p {p:.3g})" if math.isfinite(e.est) else "n/a"
            mean = sum(v for _, _, v in by_q[q]) / len(by_q[q])
            rule = next(r["rule"] for r in rows if r["question"] == q)
            lines.append(f"| {q} | {rule} | {mean:.4g} | {cells['estimand']} | {cells['placebo']} |")
    (args.out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(json.dumps({"runs": len(runs), "scored": len(rows)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
