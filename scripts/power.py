"""Post hoc power for the three registered primaries. Exploratory: not part of the registered analysis.

    .venv/bin/python scripts/power.py [--per-run outputs/arms/per_run.csv] [--out outputs/power]
                                      [--runs runs] [--studies 2000] [--draws 2000] [--seed 0]

No registered primary rejected its null (outputs/arms/summary.md). This script asks what more runs of
these two models could have changed. From an analysis's per-run table (the registered one by
default), it takes each run's value on each primary and each model's pooled within-arm variance s^2,
the variance behind the registered standard error. A balanced study of N runs puts N/8 in each model
and arm, so the registered estimate's variance is 2 (s_opus^2 + s_sol^2) / N. It writes:

- power.csv: for each primary, each true effect and each study size N, the chance of rejecting that
  primary's null, two ways:
  - `analytic`: a noncentral t approximation to the registered estimate, with Welch-Satterthwaite
    degrees of freedom, tested at 5%/3, the level Holm's correction applies to the smallest of three
    p-values;
  - `simulated`: studies built by resampling each model's within-arm residuals, whole runs at a time so
    the three primaries keep their correlation, with the true effect added to the target primary alone.
    Each study then meets the registered rule itself: randomization p-values from re-assigning arms
    within each model with each arm's count kept, Holm's correction across the three primaries, and
    rejection below 0.05;
- joint.csv: each primary's simulated power, and about the smallest study that reaches 80%, when all
  three true effects equal today's estimates at once. Once Holm's correction rejects one primary it
  tests the next at 5%/2, so these counts fall below the ones at 5%/3;
- size.csv: the same simulation with no effect anywhere, the rule's false rejections;
- second_wave.csv: the chance that pooling a second wave of N2 runs with these runs takes each primary
  past 5%/3, if its true effect equals today's estimate, and if there is none. This normal
  approximation ignores the penalty a second look owes, so it overstates what an honest test of the
  pooled runs would give;
- summary.md: the effects these runs could detect, the runs each effect would need, what those counts
  rest on, and the second-wave chances. For E1 it also signs each question's jump toward the direction
  most runs chose, lists the answers that run the other way, and quotes each one's reasoning from its
  sealed `response.json` (`--runs`), so a reader can tell a forecast from a swapped entry.

The true effects: today's estimate of each primary and, for P2 and E1, a quarter, a half and all of
its mean without its block, added to every run in the block's arms (E1 widening, as the registration
expects; P2 has no expected sign and takes +). P1 gets no such shift: without the placebo block its
runs' jumps average 0.02 points, so a shift down would push most of them below zero, where no absolute
jump can go.

These are model forecasts under stated scenarios, not observations, and this is a planning
calculation about further runs of these two models, not a finding.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
from scipy import optimize, stats  # noqa: E402

from i86 import arms  # noqa: E402

ALPHA = 0.05
POWER = 0.8
SIZES = (24, 48, 96, 192, 384, 768, 1536)  # total runs; 24 is the registered design
WAVES = (24, 48, 96, 192, 384, 768)
FRACTIONS = {0.25: "a quarter", 0.5: "half", 1.0: "all"}  # of the primary's mean without its block
EXPECTED_SIGN = {"P2 uncoached placebo": 1.0, "E1 effect index": 1.0}
# The primaries whose true effect the simulation can model as a shift of every run in the block's arms.
# P1 can't take one: its runs average 0.02 points without the block, so a shift down by a quarter or
# more of that would push most of them below zero, where no absolute jump can go.
SHIFTABLE = tuple(EXPECTED_SIGN)


# --------------------------------------------------------------------------- the registered pieces


def primary_rows(per_run_csv: Path) -> dict[str, list[tuple[str, str, float]]]:
    """{primary: [(family, arm, value)]} from an analysis's per_run.csv by arms.PRIMARY's definitions
    (each run's mean of the measure over the primary's questions), every primary's rows in one run
    order."""
    table: dict[tuple[str, str, str], dict[str, dict[str, str]]] = {}
    with open(per_run_csv, newline="") as f:
        for r in csv.DictReader(f):
            table.setdefault((r["run"], r["family"], r["arm"]), {})[r["question"]] = r
    return {name: [(fam, arm, statistics.fmean(float(q[n][measure]) for n in nodes))
                   for (_, fam, arm), q in sorted(table.items())]
            for name, nodes, measure, _ in arms.PRIMARY}


def contrast_of(name: str) -> dict[str, float]:
    return arms.CONTRASTS[next(c for n, _, _, c in arms.PRIMARY if n == name)]


def holm_rows(p: np.ndarray) -> np.ndarray:
    """Holm's adjustment of each row of p (studies x primaries), as arms.holm does for one study."""
    m = p.shape[1]
    order = np.argsort(p, axis=1, kind="stable")
    ranked = np.take_along_axis(p, order, axis=1) * (m - np.arange(m))
    ranked = np.minimum(1.0, np.maximum.accumulate(ranked, axis=1))
    out = np.empty_like(p)
    np.put_along_axis(out, order, ranked, axis=1)
    return out


def randomization(ys: list[np.ndarray], arm_idx: list[np.ndarray], weights: np.ndarray, draws: int,
                  rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """The registered estimate and randomization p-value of several contrasts on many studies at once.

    ys[f]: family f's values, studies x runs x contrasts; arm_idx[f]: each of its runs' arm, as an index
    into arms.ARMS; weights: contrasts x arms. The estimate weights each family equally and each arm
    within it by its contrast weight over its run count, as arms.effect does. The p-value re-assigns
    arms within each family `draws` times, keeping each arm's count (one set of re-assignments for every
    study and contrast, drawn apart from the data), and counts the observed assignment in, as
    arms.randomization_p does. Returns (estimates, p-values), each studies x contrasts."""
    fams = len(ys)
    studies, _, k = ys[0].shape
    est = np.zeros((studies, k))
    perm = np.zeros((draws, studies, k))
    for y, a in zip(ys, arm_idx):
        counts = np.bincount(a, minlength=len(arms.ARMS))
        coef = weights[:, a] / (fams * counts[a])  # contrasts x runs
        est += np.einsum("srk,kr->sk", y, coef)
        order = np.argsort(rng.random((draws, len(a))), axis=1)
        for j in range(k):
            perm[:, :, j] += coef[j][order] @ y[:, :, j].T
    tol = 1e-9 * np.maximum(1.0, np.abs(est))
    hits = (np.abs(perm) >= np.abs(est)[None] - tol[None]).sum(axis=0)
    return est, (hits + 1) / (draws + 1)


# --------------------------------------------------------------------------- analytic


def family_variances(rows) -> dict[str, float]:
    """Each complete family's pooled within-arm variance, as the registered standard error uses it."""
    c = arms.cells(rows)
    return {f: arms.pooled_variance(c, f)[0] for f in arms.complete_families(c)}


def family_dfs(rows) -> dict[str, int]:
    """The degrees of freedom behind each complete family's pooled variance: its runs less its arms."""
    c = arms.cells(rows)
    return {f: arms.pooled_variance(c, f)[1] for f in arms.complete_families(c)}


def variance_range(s2: dict[str, float], dfs: dict[str, int], level: float = 0.95) -> tuple[float, float]:
    """A `level` range for the true sum of the families' variances, as multiples of today's sum: the sum
    taken as a scaled chi-square with Welch-Satterthwaite degrees of freedom. A study's runs needed
    scale with this sum, so the range scales every run count."""
    total = sum(s2.values())
    nu = total ** 2 / sum(v ** 2 / dfs[f] for f, v in s2.items())
    return nu / stats.chi2.ppf(1 - (1 - level) / 2, nu), nu / stats.chi2.ppf((1 - level) / 2, nu)


def balanced_var(s2: dict[str, float], n: float, contrast: dict[str, float]) -> dict[str, float]:
    """Each family's part of the registered estimate's variance in a balanced study of n runs: with F
    families and n / (4F) runs per family and arm, s_f^2 * sum_a (w_a / F)^2 / (n / (4F))."""
    fams = len(s2)
    per_cell = n / (len(arms.ARMS) * fams)
    return {f: v * sum((w / fams) ** 2 for w in contrast.values()) / per_cell for f, v in s2.items()}


def analytic_power(delta: float, s2: dict[str, float], n: int, contrast: dict[str, float],
                   alpha: float = ALPHA / 3) -> float:
    """Two-sided power of the registered t statistic at level alpha for a true effect delta in a
    balanced study of n runs: the noncentral t with Welch-Satterthwaite degrees of freedom, each
    family's df being its runs less its four arms."""
    total = sum(balanced_var(s2, n, contrast).values())
    df = balanced_df(s2, n, contrast)
    return two_sided(stats.t.ppf(1 - alpha / 2, df), df, abs(delta) / math.sqrt(total))


def balanced_df(s2: dict[str, float], n: float, contrast: dict[str, float]) -> float:
    """The registered estimate's Welch-Satterthwaite degrees of freedom in a balanced study of n runs,
    each family's df being its runs less its four arms."""
    parts = balanced_var(s2, n, contrast)
    df_f = n / len(s2) - len(arms.ARMS)
    return sum(parts.values()) ** 2 / sum(v ** 2 / df_f for v in parts.values())


def two_sided(crit: float, df: float, ncp: float) -> float:
    """P(|T| > crit) for T noncentral t with df degrees of freedom and noncentrality ncp >= 0. SciPy's
    lower tail returns nan for some ncp of 8 or more, where its true value lies below 1e-15; it counts
    as 0 there."""
    upper, lower = stats.nct.sf(crit, df, ncp), stats.nct.cdf(-crit, df, ncp)
    if math.isnan(lower) and ncp >= 6:
        lower = 0.0
    if math.isnan(upper) or math.isnan(lower):
        raise ValueError(f"noncentral t undefined at crit {crit}, df {df}, ncp {ncp}")
    return float(upper + lower)


def runs_needed(delta: float, s2: dict[str, float], contrast: dict[str, float], alpha: float = ALPHA / 3,
                power: float = POWER, step: int = 8, cap: int = 10_000_000) -> float:
    """The smallest balanced study, in multiples of `step` runs, whose analytic power reaches `power`
    (inf past `cap`, or for no effect)."""
    if delta == 0:
        return math.inf
    z = stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power)
    per_run = sum(balanced_var(s2, 1, contrast).values())  # a study of n runs has variance per_run / n
    n = max(2 * step, int(per_run * (z / delta) ** 2) // step * step)
    while n > 2 * step and analytic_power(delta, s2, n - step, contrast, alpha) >= power:
        n -= step
    while analytic_power(delta, s2, n, contrast, alpha) < power:
        n += step
        if n > cap:
            return math.inf
    return n


def mde(s2: dict[str, float], n: int, contrast: dict[str, float], alpha: float = ALPHA / 3,
        power: float = POWER) -> float:
    """The smallest true effect a balanced study of n runs detects with the given power (analytic)."""
    lo, hi = 0.0, 1.0
    while analytic_power(hi, s2, n, contrast, alpha) < power:
        hi *= 2
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if analytic_power(mid, s2, n, contrast, alpha) < power else (lo, mid)
    return hi


def mde_from_se(se: float, df: float, alpha: float = ALPHA / 3, power: float = POWER) -> float:
    """The same for an existing estimate with standard error se and df degrees of freedom."""
    crit = stats.t.ppf(1 - alpha / 2, df)
    return optimize.brentq(lambda ncp: two_sided(crit, df, ncp) - power, 0.0, 20.0) * se


def second_wave_power(est1: float, se1: float, delta: float, s2: dict[str, float], n2: int,
                      contrast: dict[str, float], alpha: float = ALPHA / 3, df_today: float | None = None) -> float:
    """The chance that a precision-weighted pool of today's estimate est1 (standard error se1) and a
    second balanced wave of n2 runs lies beyond the two-sided critical value at alpha, given est1 and a
    true effect delta. The cutoff is the normal one, or, given today's degrees of freedom df_today,
    the t cutoff with those plus the wave's own, since both standard errors come from estimated
    variances (the normal cutoff then rejects a true null more often than alpha)."""
    i1 = 1 / se1 ** 2
    i2 = 1 / sum(balanced_var(s2, n2, contrast).values())
    mean = (i1 * est1 + i2 * delta) / math.sqrt(i1 + i2)
    sd = math.sqrt(i2 / (i1 + i2))
    if df_today is None:
        z = stats.norm.ppf(1 - alpha / 2)
    else:
        # A wave with one run per family and arm leaves no within-arm spread, so it adds no df.
        wave_df = balanced_df(s2, n2, contrast) if n2 / len(s2) > len(arms.ARMS) else 0.0
        z = stats.t.ppf(1 - alpha / 2, df_today + wave_df)
    return float(stats.norm.sf((z - mean) / sd) + stats.norm.cdf((-z - mean) / sd))


def wave_needed(est1, se1, delta, s2, contrast, alpha=ALPHA / 3, power=POWER, step=8, cap=10_000_000,
                df_today=None) -> float:
    """The smallest second wave, in multiples of `step` runs, that second_wave_power puts at `power`."""
    hi = step
    while second_wave_power(est1, se1, delta, s2, hi, contrast, alpha, df_today) < power:
        hi *= 2
        if hi > cap:
            return math.inf
    lo = 0
    while hi - lo > step:
        mid = (lo + hi) // 2 // step * step
        if mid <= lo:
            mid = lo + step
        if second_wave_power(est1, se1, delta, s2, mid, contrast, alpha, df_today) >= power:
            hi = mid
        else:
            lo = mid
    return hi


# --------------------------------------------------------------------------- what the counts rest on


# The registered jump as weights on a run's answers at the six closed ranges, lowest first: two
# least-squares lines through three points each, evaluated at 50%, Peltola's minus Sullivan's.
JUMP_WEIGHTS = (5 / 12, -4 / 12, -13 / 12, 13 / 12, 4 / 12, -5 / 12)


def step_score(boundary: int) -> float:
    """What the registered jump makes of a step of 1 in a run's answers between closed ranges boundary
    - 1 and boundary (0 = below the lowest closed range, 6 = above the highest): the summed weights
    of the ranges above the step."""
    return sum(JUMP_WEIGHTS[boundary:])


def step_words(score: float) -> str:
    """A step's score in twelfths of its size D: 'D', '-D/12', '-5D/12', '0'."""
    k = round(score * 12)
    if k == 0:
        return "0"
    if k == 12:
        return "D"
    return f"{'-' if k < 0 else ''}{'' if abs(k) == 1 else abs(k)}D/12"


def oriented_index(per_run_csv: Path, swapped=frozenset()) -> list[tuple[str, str, float]] | None:
    """E1's index with each question's jump signed toward the direction most runs chose (the sign of the
    question's median jump), in place of the absolute jump, which scores an answer the same whichever
    way round its forecaster entered it. `swapped` names (run, question) answers to read the other way
    round first, as if their forecaster had entered the two sides' values in each other's ranges.
    Exploratory; None if the table lacks signed jumps."""
    name, nodes, _, _ = next(p for p in arms.PRIMARY if p[0] == "E1 effect index")
    table: dict[tuple[str, str, str], dict[str, dict[str, str]]] = {}
    with open(per_run_csv, newline="") as f:
        for r in csv.DictReader(f):
            if "jump" not in r:
                return None
            table.setdefault((r["run"], r["family"], r["arm"]), {})[r["question"]] = r
    jumps = {key: {n: (-1.0 if (key[0], n) in swapped else 1.0) * float(q[n]["jump"]) for n in nodes}
             for key, q in table.items()}
    sign = {n: math.copysign(1.0, statistics.median(j[n] for j in jumps.values())) for n in nodes}
    return [(fam, arm, statistics.fmean(sign[n] * j[n] for n in nodes))
            for (_, fam, arm), j in sorted(jumps.items())]


def answer_reasoning(runs_dir: Path, run: str, question: str) -> str | None:
    """The reasoning a run's sealed answer gives for one question, on one line, or None if the run's
    response.json lacks it."""
    try:
        nodes = json.loads((Path(runs_dir) / run / "response.json").read_text()).get("nodes")
    except (OSError, ValueError, AttributeError):
        return None
    node = nodes.get(question) if isinstance(nodes, dict) else None
    text = node.get("reasoning") if isinstance(node, dict) else None
    return " ".join(text.split()) if isinstance(text, str) and text.strip() else None


# What a run's sealed session log shows about an answer that runs against its question's usual direction,
# for the answers whose logs I have read (prereg/README.md gives each log's line numbers and hash).
SEALED_RECORDS = {
    ("sol-06", "sullivan_post"): (
        "Its sealed session log shows a slip: a sub-agent advised 0.25 to 0.30 after a defeat and 0.04 to "
        "0.07 after reelection, and the run then entered 0.28 in the four ranges where Sullivan wins "
        "(`prereg/README.md`)."),
}


def listed(items: list[str]) -> str:
    """'a, b and c'."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def runs_words(need: float) -> str:
    """A run count for a table: the search's floor, two runs per model and arm, reads "16 or fewer"."""
    return "16 or fewer" if need == 16 else fmt(need)


def reversed_answers(per_run_csv: Path, at_least: float = 0.1) -> list[tuple[str, str, float]]:
    """(run, question, jump) for each of E1's answers whose jump runs against its question's median
    direction by at least `at_least`."""
    _, nodes, _, _ = next(p for p in arms.PRIMARY if p[0] == "E1 effect index")
    with open(per_run_csv, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["question"] in nodes]
    out = []
    for n in nodes:
        jumps = [(r["run"], float(r["jump"])) for r in rows if r["question"] == n]
        direction = math.copysign(1.0, statistics.median(j for _, j in jumps))
        out += [(run, n, j) for run, j in jumps if direction * j <= -at_least]
    return out


# --------------------------------------------------------------------------- simulation


class Simulator:
    """Studies of n runs built from the observed runs, judged by the registered rule.

    Within each family, the simulator scales every run's residual vector (its primaries less its own
    arm's means) by sqrt(runs / (runs - 4)), so the resampled residuals' variance matches the pooled
    within-arm variance the registered standard error uses. A study draws n / 8 residual vectors per family and
    arm with replacement, adds the family's mean and the true effect (on the target primary, or on
    each primary that a dict of effects names, through its contrast's weights), then takes each
    primary's randomization p-value (`randomization`) and Holm's correction across the primaries."""

    def __init__(self, primaries: dict[str, list[tuple[str, str, float]]], seed: int = 0):
        self.names = list(primaries)
        first = primaries[self.names[0]]
        values = np.array([[v for _, _, v in primaries[nm]] for nm in self.names]).T  # runs x primaries
        self.families = arms.complete_families(arms.cells([(f, a, 0.0) for f, a, _ in first]))
        self.resid, self.mean = {}, {}
        for fam in self.families:
            idx = [i for i, (f, _, _) in enumerate(first) if f == fam]
            arm = np.array([arms.ARMS.index(first[i][1]) for i in idx])
            y = values[idx]
            cell_mean = np.array([y[arm == j].mean(axis=0) for j in range(len(arms.ARMS))])
            self.resid[fam] = (y - cell_mean[arm]) * math.sqrt(len(idx) / (len(idx) - len(arms.ARMS)))
            self.mean[fam] = y.mean(axis=0)
        self.weights = np.array([[contrast_of(nm)[a] for a in arms.ARMS] for nm in self.names])
        self.rng = np.random.default_rng(seed)

    def reject(self, n: int, target: str | dict[str, float] | None, delta: float, studies: int, draws: int,
               batch: int = 100) -> np.ndarray:
        """studies x primaries: whether Holm's correction rejects each primary in each study. Each batch
        of studies gets its own set of re-assignments, so no one set's luck sets the rule's level."""
        if studies > batch:
            return np.vstack([self.reject(n, target, delta, min(batch, studies - i), draws, batch)
                              for i in range(0, studies, batch)])
        ys, arm = self.draw(n, target, delta, studies)
        _, p = randomization(ys, [arm] * len(ys), self.weights, draws, self.rng)
        return holm_rows(p) < ALPHA

    def draw(self, n: int, target: str | dict[str, float] | None, delta: float,
             studies: int) -> tuple[list[np.ndarray], np.ndarray]:
        """`studies` balanced studies of n runs: each family's values (studies x runs x primaries) and the
        runs' arms (indices into arms.ARMS, n / 8 each, the same in every family)."""
        fams = len(self.families)
        if n % (len(arms.ARMS) * fams):
            raise ValueError(f"{n} runs don't split evenly over {fams} families and {len(arms.ARMS)} arms")
        arm = np.repeat(np.arange(len(arms.ARMS)), n // (len(arms.ARMS) * fams))
        shift = np.zeros(len(self.names))
        if isinstance(target, dict):  # several primaries at once, each with its own effect
            for name, effect in target.items():
                shift[self.names.index(name)] = effect
        elif target is not None:
            shift[self.names.index(target)] = delta
        ys = []
        for fam in self.families:
            pick = self.rng.integers(0, len(self.resid[fam]), size=(studies, len(arm)))
            ys.append(self.mean[fam] + self.resid[fam][pick] + shift * self.weights[:, arm].T)
        return ys, arm

    def power(self, n: int, target: str | dict[str, float] | None, delta: float, studies: int,
              draws: int) -> np.ndarray:
        return self.reject(n, target, delta, studies, draws).mean(axis=0)


def simulated_runs_needed(sim: Simulator, effects: dict[str, float], name: str, upper: int, studies: int,
                          draws: int, power: float = POWER, step: int = 8, cap: int = 100_000) -> float:
    """About the smallest study, in multiples of `step` runs, at which the simulated rule rejects `name`
    at least `power` of the time when every primary in `effects` has its effect at once. A bisection
    between 2 * step runs and `upper`, which grows by half until the rule reaches `power` there (inf past
    `cap`). Each size's power carries Monte Carlo error, so the answer can sit a few steps off."""
    i = sim.names.index(name)

    def reaches(n: int) -> bool:
        return float(sim.power(n, effects, 0.0, studies, draws)[i]) >= power

    lo, hi = 2 * step, max(upper // step * step, 3 * step)
    while not reaches(hi):
        lo, hi = hi, hi * 3 // (2 * step) * step
        if hi > cap:
            return math.inf
    if lo == 2 * step and reaches(lo):
        return lo
    while hi - lo > step:
        mid = (lo + hi) // (2 * step) * step
        lo, hi = (lo, mid) if reaches(mid) else (mid, hi)
    return hi


# --------------------------------------------------------------------------- the report


def fmt(x) -> str:
    if isinstance(x, float) and math.isinf(x):
        return "over 10,000,000"
    if isinstance(x, (int, np.integer)):
        return f"{int(x):,}"
    return f"{x:.3g}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--per-run", type=Path, default=ROOT / "outputs" / "arms" / "per_run.csv")
    ap.add_argument("--effects", type=Path, default=None, help="default: effects.csv beside --per-run")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs" / "power")
    ap.add_argument("--runs", type=Path, default=ROOT / "runs", help="the sealed runs, for reversed answers' reasoning")
    ap.add_argument("--studies", type=int, default=2000)
    ap.add_argument("--draws", type=int, default=2000)
    ap.add_argument("--sizes", type=int, nargs="*", default=list(SIZES))
    ap.add_argument("--simulate-max", type=int, default=768, help="simulate studies up to this many runs")
    ap.add_argument("--waves", type=int, nargs="*", default=list(WAVES))
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    effects_csv = a.effects or a.per_run.with_name("effects.csv")
    sizes = sorted(set(a.sizes))

    primaries = primary_rows(a.per_run)
    with open(effects_csv, newline="") as f:
        registered = {r["outcome"]: r for r in csv.DictReader(f) if r.get("registered_primary") == "True"}
    sim = Simulator(primaries, seed=a.seed)
    a.out.mkdir(parents=True, exist_ok=True)

    info = {}
    for name, rows in primaries.items():
        con = contrast_of(name)
        e = arms.effect(rows, con)
        reg = registered[name]
        if not (math.isclose(e.est, float(reg["estimate"]), rel_tol=1e-9, abs_tol=1e-12)
                and math.isclose(e.se, float(reg["se"]), rel_tol=1e-9)):
            raise SystemExit(f"{name}: per_run.csv gives {e.est} (se {e.se}); effects.csv says "
                             f"{reg['estimate']} (se {reg['se']})")
        c = arms.cells(rows)
        fams = arms.complete_families(c)
        s2 = family_variances(rows)
        if len(s2) != len(arms.FAMILIES) or not all(math.isfinite(v) and v > 0 for v in s2.values()):
            raise SystemExit(f"{name}: needs both families' runs to vary within arms; pooled variances {s2}")
        without = arms.combination(c, {(f, arm): 0.5 / len(fams) for f in fams for arm, w in con.items() if w < 0})
        deltas = {"today's estimate": e.est}
        if name in SHIFTABLE:
            for frac, words in FRACTIONS.items():
                deltas[f"{words} of its mean without the block"] = EXPECTED_SIGN[name] * frac * without.est
        info[name] = {"con": con, "est": e.est, "se": e.se, "df": e.df, "p": float(reg["randomization_p"]),
                      "holm": float(reg["holm_p"]), "s2": s2, "dfs": family_dfs(rows), "without": without.est,
                      "deltas": deltas, "runs": sum(len(v) for v in c.values()),
                      "ci": (float(reg["ci_lo"]), float(reg["ci_hi"])) if "ci_lo" in reg else None}

    power_rows = []
    for name, d in info.items():
        for label, delta in d["deltas"].items():
            need = runs_needed(delta, d["s2"], d["con"])
            grid = sorted(set(sizes) | ({need} if math.isfinite(need) and need <= a.simulate_max else set()))
            for n in grid:
                simulated = (float(sim.power(n, name, delta, a.studies, a.draws)[sim.names.index(name)])
                             if n <= a.simulate_max else math.nan)
                power_rows.append({"primary": name, "effect": label, "delta": delta, "runs": n,
                                   "analytic": analytic_power(delta, d["s2"], n, d["con"]),
                                   "simulated": simulated, "runs_needed_analytic": need})
    size_rows = []
    for n in (n for n in sizes if n <= a.simulate_max):
        rej = sim.reject(n, None, 0.0, a.studies, a.draws)
        size_rows.append({"runs": n, **{nm: float(r) for nm, r in zip(sim.names, rej.mean(axis=0))},
                          "any": float(rej.any(axis=1).mean())})
    wave_rows = [{"primary": name, "second_wave_runs": n2, "true_effect": d["est"],
                  "chance_past_holm_level": second_wave_power(d["est"], d["se"], d["est"], d["s2"], n2, d["con"]),
                  "chance_past_holm_level_t_cutoff": second_wave_power(d["est"], d["se"], d["est"], d["s2"], n2,
                                                                       d["con"], df_today=d["df"]),
                  "chance_if_no_effect": second_wave_power(d["est"], d["se"], 0.0, d["s2"], n2, d["con"]),
                  "chance_if_no_effect_t_cutoff": second_wave_power(d["est"], d["se"], 0.0, d["s2"], n2, d["con"],
                                                                    df_today=d["df"])}
                 for name, d in info.items() for n2 in a.waves]
    joint = {name: d["est"] for name, d in info.items()}
    joint_rows = []
    for name, d in info.items():
        need = runs_needed(d["est"], d["s2"], d["con"])
        if math.isfinite(need) and need <= a.simulate_max:
            there = float(sim.power(need, joint, 0.0, a.studies, a.draws)[sim.names.index(name)])
            joint_rows.append({"primary": name, "true_effect": d["est"], "runs_needed_alone_analytic": need,
                               "simulated_power_there_all_three": there,
                               "runs_needed_all_three_simulated": simulated_runs_needed(
                                   sim, joint, name, need, a.studies, a.draws)})
    tables = [("power.csv", power_rows), ("size.csv", size_rows), ("second_wave.csv", wave_rows)]
    if len(joint_rows) == len(info):
        tables.append(("joint.csv", joint_rows))
    for fname, table in tables:
        with open(a.out / fname, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(table[0]))
            w.writeheader()
            w.writerows(table)

    source = a.per_run.relative_to(ROOT) if a.per_run.is_relative_to(ROOT) else a.per_run
    L = ["# What more runs could change", "",
         f"Exploratory and post hoc: a planning calculation from `{source}` by `scripts/power.py`, not part "
         "of the registered analysis. These are model forecasts under stated scenarios, not observations.", "",
         "## Today's estimates", "",
         "| primary | runs | estimate | standard error | mean without the block | randomization p | Holm p |",
         "|---|---|---|---|---|---|---|"]
    for name, d in info.items():
        L.append(f"| {name} | {d['runs']} | {fmt(d['est'])} | {fmt(d['se'])} | {fmt(d['without'])} | "
                 f"{d['p']:.3f} | {d['holm']:.3f} |")
    L += ["", "## The smallest effect a study detects 80% of the time", "",
          "Analytic, at 5%/3, the level Holm's correction applies to the smallest of three p-values. The "
          "first column uses today's standard error and degrees of freedom; the others, balanced studies.", "",
          "| primary | today's runs | " + " | ".join(f"{n:,} runs" for n in sizes) + " |",
          "|---|---|" + "---|" * len(sizes)]
    for name, d in info.items():
        L.append(f"| {name} | {fmt(mde_from_se(d['se'], d['df']))} | "
                 + " | ".join(fmt(mde(d["s2"], n, d["con"])) for n in sizes) + " |")
    L += ["", "## Runs a study needs for an 80% chance of rejecting", "",
          "Analytic, at 5%/3, in a balanced study, with the other two primaries' effects at zero. `simulated` "
          "gives the registered rule's own power (randomization p-values, Holm's correction across the three "
          "primaries) at that many runs under the same condition, where that is at most the largest simulated "
          "size. The search starts at 16 runs, two per model and arm.", "",
          "| primary | true effect | size | runs needed | simulated power there |", "|---|---|---|---|---|"]
    for name, d in info.items():
        for label, delta in d["deltas"].items():
            need = runs_needed(delta, d["s2"], d["con"])
            got = next((r["simulated"] for r in power_rows
                        if r["primary"] == name and r["effect"] == label and r["runs"] == need), None)
            L.append(f"| {name} | {label} | {fmt(delta)} | {runs_words(need)} | "
                     f"{'' if got is None else f'{got:.2f}'} |")
    if len(joint_rows) == len(info):
        short = [r["primary"].split()[0] for r in joint_rows]
        L += ["", "If all three true effects equal today's estimates at once, a study needs fewer runs: once "
              "Holm's correction rejects one primary, it tests the next at 5%/2 and the last at 5%. The simulated "
              "rule then reaches 80% at about "
              + listed([f"{fmt(r['runs_needed_all_three_simulated'])} runs for {s}" for r, s in zip(joint_rows, short)])
              + ". At the runs in the table above, it rejects "
              + listed([f"{s} {100 * r['simulated_power_there_all_three']:.0f}%" for r, s in zip(joint_rows, short)])
              + " of the time (`joint.csv`). Each simulated count carries Monte Carlo error of a few steps of 8 runs."]
    L += ["", "## What the run counts rest on", "",
          "- **They restate the p-values.** At today's estimate, the runs needed follow from how many standard "
          "errors that estimate lies from zero (" + ", ".join(f"{n.split()[0]} {d['est'] / d['se']:.2f}" for n, d in info.items())
          + "), and they scale as one over the effect squared. If the true effect sat at an end "
          "of today's 95% interval, a balanced study would need the runs below (for P1 only a rough guide, "
          "since a shift models it badly; see the third point). An end that would push a mean absolute jump "
          "below zero reads \"not possible\":", "",
          "  | primary | 95% interval | runs needed at its lower end | at its upper end |",
          "  |---|---|---|---|"]
    for name, d in info.items():
        if d["ci"]:
            lo, hi = d["ci"]
            ends = ["not possible" if end < -d["without"] else runs_words(runs_needed(end, d["s2"], d["con"]))
                    for end in (lo, hi)]
            L.append(f"  | {name} | {fmt(lo)} to {fmt(hi)} | {ends[0]} | {ends[1]} |")
    ranges = {n: variance_range(d["s2"], d["dfs"]) for n, d in info.items()}
    dfs = next(iter(info.values()))["dfs"]
    L += ["", "- **The variances are noisy.** The two models' pooled variances rest on "
          + " and ".join(f"{v} ({f})" for f, v in dfs.items())
          + " degrees of freedom. A 95% range for their sum scales every run count above by "
          + "; ".join(f"{lo:.2f} to {hi:.2f} for {n.split()[0]}" for n, (lo, hi) in ranges.items()) + "."]
    p1 = [v for _, _, v in primaries["P1 coached placebo"]]
    on_grid = all(abs(v * 240 - round(v * 240)) < 1e-6 for v in p1)
    L += ["- **A shift fits E1 better than P1 or P2.** The simulation adds the effect to every run in the "
          "block's arms. "
          + (f"P1's jumps take the values the forecasters' rounding allows, each a multiple of 1/240 of a "
             f"point, and {sum(abs(v) < 1e-9 for v in p1)} of {len(p1)} are zero, so this report gives P1 no "
             "shifted effect beyond today's estimate, and its simulated power there serves only as a rough "
             "guide. " if on_grid else "")
          + "P2's measure scores a step of D in a run's governor's-race answers by where it falls: "
          + "; ".join(f"{step_words(step_score(b))} at {x}" for b, x in
                      ((3, "50%"), (2, "49.5% or 50.5%"), (1, "49% or 51%"), (0, "48.5% or 51.5%")))
          + ". Its arm means move with how many runs put their step where, not as a shift."]
    oriented = oriented_index(a.per_run)
    if oriented is not None:
        flipped = reversed_answers(a.per_run)
        e1 = info["E1 effect index"]

        def reading(rows) -> str:
            e = arms.effect(rows, arms.MAIN_E)
            return f"{e.est:.4f} (standard error {e.se:.4f}, estimate over standard error {e.est / e.se:.2f})"

        head = ("- **E1's absolute jumps ignore each jump's direction,** so they score an answer the same "
                "whichever way round its forecaster entered it. Signed toward the direction most runs chose, ")
        if flipped:
            back = oriented_index(a.per_run, swapped={(run, q) for run, q, _ in flipped})
            L += [head + f"with the answers below read the other way round, E1 reads {reading(back)}, beside "
                  f"{e1['est']:.4f} from the registered absolute jumps. With those answers as written, it reads "
                  f"{reading(oriented)}, which counts each one as a forecast against the other runs. Exploratory; "
                  "the registered measure stands. The answers that run against their question's usual direction "
                  "by at least 0.1 (a jump is Peltola's side minus Sullivan's), each with its run's own reasoning "
                  "for it:"]
            for run, q, j in flipped:
                why = answer_reasoning(a.runs, run, q)
                record = SEALED_RECORDS.get((run, q))
                L.append(f"  - {run}, {q}: jump {j:+.2f}, a higher chance where "
                         f"{'Sullivan' if j < 0 else 'Peltola'} wins. "
                         + (f'Its reasoning: "{why}"' if why else "Its answer gives no reasoning.")
                         + (f" {record}" if record else ""))
        else:
            L += [head + f"E1 reads {reading(oriented)}, beside {e1['est']:.4f} from the registered absolute "
                  "jumps; no answer runs against its question's usual direction by 0.1 or more. Exploratory; the "
                  "registered measure stands."]
    L += ["", "## A pooled second wave", "",
          "The chance that today's runs pooled with a second wave cross 5%/3, if the true effect equals "
          "today's estimate, with the normal cutoff and then the t cutoff (today's degrees of freedom plus "
          "the wave's, a few fewer than one pooled analysis of all the runs would have). Both standard errors come from estimated variances, so the normal cutoff rejects a "
          "true null more often than 5%/3. Neither counts the penalty a second look owes, so both overstate "
          "what an honest pooled test gives.", "",
          "| primary | " + " | ".join(f"+{n:,} runs" for n in a.waves) + " | runs for 80% |",
          "|---|" + "---|" * (len(a.waves) + 1)]
    for name, d in info.items():
        cells = [f"{second_wave_power(d['est'], d['se'], d['est'], d['s2'], n, d['con']):.2f} / "
                 f"{second_wave_power(d['est'], d['se'], d['est'], d['s2'], n, d['con'], df_today=d['df']):.2f}"
                 for n in a.waves]
        L.append(f"| {name} | " + " | ".join(cells)
                 + f" | {fmt(wave_needed(d['est'], d['se'], d['est'], d['s2'], d['con']))} / "
                 + f"{fmt(wave_needed(d['est'], d['se'], d['est'], d['s2'], d['con'], df_today=d['df']))} |")
    L += ["", "## The rule's false rejections", "",
          f"With no effect anywhere, the share of {a.studies:,} simulated studies in which Holm's correction "
          "rejected each primary, and any of them. The registered rule holds the last at or below 5%, and a "
          f"simulated share can stray from it by Monte Carlo error, about {100 * math.sqrt(0.05 * 0.95 / a.studies):.1f} "
          "points here.", "",
          "| runs | " + " | ".join(sim.names) + " | any |", "|---|" + "---|" * (len(sim.names) + 1)]
    for r in size_rows:
        L.append(f"| {r['runs']:,} | " + " | ".join(f"{r[nm]:.3f}" for nm in sim.names) + f" | {r['any']:.3f} |")
    L += ["", f"Simulation: {a.studies:,} studies per cell, {a.draws:,} re-assignments per study, seed {a.seed}.", ""]
    (a.out / "summary.md").write_text("\n".join(L))
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
