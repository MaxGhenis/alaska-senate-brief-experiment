"""What the briefs' two blocks change in the forecasts: the study's registered analysis.

For each run and each question asked at every range, take the run's answer at each range (the
probability for a yes/no question, the median for a quantity) and compute:

- `contrast`: the answer at the lowest Peltola-win range minus the answer at the highest Sullivan-win
  range (dem_50.0_50.5 minus dem_49.5_50.0), the contrast at the cutoff;
- `jump`: the gap at 50% between least-squares lines through the three closed ranges on each side, at
  the ranges' midpoints (the open tails stay out of the fit);
- `slope`: the mean of the two lines' slopes, per percentage point of Peltola's share;
- `abs_jump`: the jump's absolute value.

An arm's mean weights the two model families equally: the mean of the Opus runs' mean and the Sol
runs' mean. An effect of a block is the same kind of linear combination of the eight family-by-arm
means: `MAIN_E` (estimand block in minus out, averaged over the placebo block), `MAIN_P` (placebo block
in minus out, averaged over the estimand block) and `INTERACTION`. Its variance is
sum_f s_f^2 * sum_a w_fa^2 / n_fa, where s_f^2 pools family f's runs' variance about their own arm's
mean over its four arms, with n_f - 4 degrees of freedom, and the two families' degrees of freedom
combine by Welch-Satterthwaite. A family left without a run in some arm drops out of every estimate,
and the other family's runs carry them alone; with neither family complete, the estimates are
undefined (nan). The randomization p-value re-assigns the arms among each family's runs, keeping each
arm's count, which is exact under the sharp null that the briefs change nothing: neither any run's
answers nor whether it stays in the analysis.

These are model forecasts under stated scenarios, not observations: a jump is what a forecaster
expects a bare win to change, not a measured effect.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass

from .graph import Graph, Node
from .response import Forecasts

FAMILIES = ("opus", "sol")
ARMS = ("00", "01", "10", "11")
MAIN_E = {"10": 0.5, "11": 0.5, "00": -0.5, "01": -0.5}
MAIN_P = {"01": 0.5, "11": 0.5, "00": -0.5, "10": -0.5}
INTERACTION = {"11": 1.0, "00": 1.0, "10": -1.0, "01": -1.0}
CONTRASTS = {"estimand": MAIN_E, "placebo": MAIN_P, "interaction": INTERACTION}
MEASURES = ("contrast", "jump", "abs_jump", "slope")

# The registered primary outcomes: (name, nodes averaged per run, measure, contrast).
PLACEBO_COACHED = "bm2_yes_share"
PLACEBO_UNCOACHED = "gov_dem_share"
EFFECT_INDEX = ("recount", "contest", "court_order", "winner_sworn", "sullivan_post")
PRIMARY = (
    ("P1 coached placebo", (PLACEBO_COACHED,), "abs_jump", "placebo"),
    ("P2 uncoached placebo", (PLACEBO_UNCOACHED,), "abs_jump", "placebo"),
    ("E1 effect index", EFFECT_INDEX, "abs_jump", "estimand"),
)


def holm(ps: dict[str, float]) -> dict[str, float]:
    """Holm's step-down adjustment of the registered primaries' p-values: the k-th smallest of m is
    multiplied by m - k + 1, capped at 1, and never falls below the one before it. An undefined p-value
    (nan) sorts last, still counts toward m, and stays undefined."""
    order = sorted(ps, key=lambda k: (math.isnan(ps[k]), 0.0 if math.isnan(ps[k]) else ps[k]))
    out, running = {}, 0.0
    for i, k in enumerate(order):
        if math.isnan(ps[k]):
            out[k] = math.nan
            continue
        running = max(running, min(1.0, (len(ps) - i) * ps[k]))
        out[k] = running
    return out


def line_at(xs, ys, x0: float) -> float:
    """The least-squares line through (xs, ys), evaluated at x0."""
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my + slope * (x0 - mx)


def slope_of(xs, ys) -> float:
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)


def fitted_jump(left_xs, left_ys, right_xs, right_ys, at: float) -> float:
    """The right line's value at `at` minus the left line's."""
    return line_at(right_xs, right_ys, at) - line_at(left_xs, left_ys, at)


def answer(node: Node, value) -> float:
    """A run's answer at one range: the probability, or the median of a quantity."""
    if node.kind == "binary":
        return float(value)
    if node.kind == "quantity":
        return float(value[2])  # p50: the map's quantiles are 0.05, 0.25, 0.50, 0.75, 0.95
    raise ValueError(f"{node.id}: only yes/no questions and quantities have a jump")


def ranged_nodes(graph: Graph) -> list[Node]:
    """The questions asked at every range."""
    return [n for n in graph.forecast_nodes() if n.cells_mode == "all" and n.kind in {"binary", "quantity"}]


def midpoint_pct(graph: Graph, cell: float) -> float:
    lo, hi = graph.ranges[cell]
    return (lo + hi) / 2 * 100


def run_measures(graph: Graph, fc: Forecasts) -> dict[str, dict[str, float]]:
    """{node: {contrast, jump, abs_jump, slope}} for one run."""
    if graph.quantiles[2] != 0.5:
        raise ValueError("the median must be the third quantile")
    left, right = graph.closed("fail"), graph.closed("pass")
    if len(left) < 2 or len(right) < 2:
        raise ValueError("each side needs two or more closed ranges")
    lx = [midpoint_pct(graph, c) for c in left]
    rx = [midpoint_pct(graph, c) for c in right]
    out = {}
    for node in ranged_nodes(graph):
        vals = fc.values[node.id]
        y = {c: answer(node, vals[graph.label(c)]) for c in graph.cells}
        ly, ry = [y[c] for c in left], [y[c] for c in right]
        jump = fitted_jump(lx, ly, rx, ry, 50.0)
        out[node.id] = {
            "contrast": y[min(graph.pass_cells)] - y[max(graph.fail_cells)],
            "jump": jump,
            "abs_jump": abs(jump),
            "slope": (slope_of(lx, ly) + slope_of(rx, ry)) / 2,
        }
    return out


def index(measures: dict[str, dict[str, float]], nodes, measure: str) -> float:
    """A run's mean of one measure over several questions."""
    return statistics.fmean(measures[n][measure] for n in nodes)


@dataclass(frozen=True)
class Estimate:
    est: float
    se: float
    df: float
    n: dict  # runs behind the estimate, by family
    families: tuple = FAMILIES  # the families it uses

    @property
    def t(self) -> float:
        return self.est / self.se if self.se > 0 else math.nan


def cells(rows) -> dict[tuple[str, str], list[float]]:
    """rows: [(family, arm, value)] -> {(family, arm): [values]} over every family and arm."""
    out = {(f, a): [] for f in FAMILIES for a in ARMS}
    for f, a, v in rows:
        out[(f, a)].append(float(v))
    return out


def complete_families(c) -> tuple[str, ...]:
    """The families with a run in every arm. The registered estimates use these alone, so a family that
    lost every run of some arm drops out of all of them."""
    return tuple(f for f in FAMILIES if all(c.get((f, a)) for a in ARMS))


def pooled_variance(c, family: str) -> tuple[float, int]:
    """A family's runs' variance about their own arm's mean, pooled over its arms, and its degrees of
    freedom: the family's runs less its arms with runs. (nan, 0) when no arm has two runs."""
    groups = [c[(family, a)] for a in ARMS if c.get((family, a))]
    df = sum(len(v) for v in groups) - len(groups)
    if df <= 0:
        return math.nan, 0
    return sum((x - statistics.fmean(v)) ** 2 for v in groups for x in v) / df, df


def combination(c, weights: dict[tuple[str, str], float]) -> Estimate:
    """sum of w * mean(c[(family, arm)]) over the weighted cells, with variance
    sum_f s_f^2 * sum_a w_fa^2 / n_fa from each family's pooled within-arm variance s_f^2, and
    Welch-Satterthwaite degrees of freedom across families. A weighted cell with no runs, or no weighted
    cell at all, leaves everything undefined (nan); a family with no within-arm variance to pool leaves
    the standard error and df undefined."""
    used = {k: w for k, w in weights.items() if w}
    families = tuple(dict.fromkeys(f for f, _ in used))
    n = {f: sum(len(c.get(k, ())) for k in used if k[0] == f) for f in families}
    if not used or any(not c.get(k) for k in used):
        return Estimate(math.nan, math.nan, math.nan, n, families)
    est = sum(w * statistics.fmean(c[k]) for k, w in used.items())
    parts = []
    for f in families:
        s2, df = pooled_variance(c, f)
        if df == 0:
            return Estimate(est, math.nan, math.nan, n, families)
        parts.append((s2 * sum(w * w / len(c[k]) for k, w in used.items() if k[0] == f), df))
    var = sum(v for v, _ in parts)
    top = max(v for v, _ in parts)
    if top == 0:  # no variance anywhere: no t interval
        return Estimate(est, 0.0, math.nan, n, families)
    # Welch-Satterthwaite on variances scaled by the largest, so tiny ones don't underflow when squared.
    df = sum(v / top for v, _ in parts) ** 2 / sum((v / top) ** 2 / d for v, d in parts)
    return Estimate(est, math.sqrt(var), df, n, families)


def arm_mean(rows, arm: str) -> Estimate:
    """One arm's mean over the complete families, each weighted equally."""
    c = cells(rows)
    fams = complete_families(c)
    return combination(c, {(f, arm): 1 / len(fams) for f in fams})


def effect(rows, contrast: dict[str, float]) -> Estimate:
    """A contrast of arm means over the complete families, each weighted equally."""
    c = cells(rows)
    fams = complete_families(c)
    return combination(c, {(f, a): w / len(fams) for f in fams for a, w in contrast.items()})


def family_effect(rows, contrast: dict[str, float], family: str) -> Estimate:
    """A contrast of one family's arm means (exploratory: the registered effects weight both families)."""
    c = cells(rows)
    return combination(c, {(family, a): w for a, w in contrast.items()})


def _point(by_family: dict[str, tuple[list[str], list[float]]], contrast: dict[str, float]) -> float:
    """effect(...).est from each complete family's arms and values, without standard errors."""
    total = 0.0
    for arms, values in by_family.values():
        sums = dict.fromkeys(ARMS, 0.0)
        counts = dict.fromkeys(ARMS, 0)
        for a, v in zip(arms, values):
            sums[a] += v
            counts[a] += 1
        for a, w in contrast.items():
            if not counts[a]:
                return math.nan
            total += w / len(by_family) * sums[a] / counts[a]
    return total


def randomization_p(rows, contrast: dict[str, float], draws: int = 100_000, seed: int = 0) -> float:
    """Two-sided p-value: the share of re-assignments of arms within each complete family, keeping each
    arm's count, whose effect is at least as far from zero as the observed one (the observed assignment
    counted in), by Monte Carlo. Undefined (nan) with no complete family."""
    rows = [(f, a, float(v)) for f, a, v in rows]
    # Re-assignment keeps each arm's count within each family, so the complete families stay complete.
    fams = complete_families(cells(rows))
    if not fams:
        return math.nan
    by_family = {f: ([a for g, a, _ in rows if g == f], [v for g, _, v in rows if g == f]) for f in fams}
    observed = _point(by_family, contrast)
    if math.isnan(observed):
        return math.nan
    rng = random.Random(seed)
    tol = 1e-9 * max(1.0, abs(observed))
    hits = 0
    for _ in range(draws):
        shuffled = {}
        for f, (arms, values) in by_family.items():
            arms = list(arms)
            rng.shuffle(arms)
            shuffled[f] = (arms, values)
        if abs(_point(shuffled, contrast)) >= abs(observed) - tol:
            hits += 1
    return (hits + 1) / (draws + 1)
