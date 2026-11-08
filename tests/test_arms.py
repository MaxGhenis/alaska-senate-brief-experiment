"""Properties of this study's machinery: range cells, the four briefs, the assignment and the arms
analysis (src/i86/arms.py). Every forecast here is invented MOCK data."""

from __future__ import annotations

import copy
import csv
import json
import math
from pathlib import Path

import pytest
import yaml
from hypothesis import assume, given, settings, strategies as st
from scipy import stats

from i86 import arms, cli, graph as graph_mod, prompt
from i86.graph import GRAPH_PATH, GraphError
from i86.response import parse, quantile_key, template

FIXTURES = Path(__file__).parent / "fixtures"
num = st.floats(min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False)
LEFT_X, RIGHT_X = [48.75, 49.25, 49.75], [50.25, 50.75, 51.25]


def close(a: float, b: float, scale: float = 1.0) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9 * max(1.0, scale))


# --- the fitted jump -----------------------------------------------------------------------------

@given(a=num, b=num)
def test_mock_points_on_one_line_have_no_jump(a, b):
    f = lambda x: a + b * (x - 50)  # noqa: E731
    jump = arms.fitted_jump(LEFT_X, [f(x) for x in LEFT_X], RIGHT_X, [f(x) for x in RIGHT_X], 50.0)
    assert close(jump, 0.0, abs(a) + abs(b))


@given(a=num, b=num, c=num)
def test_mock_a_step_on_a_common_slope_is_the_jump(a, b, c):
    f = lambda x: a + b * (x - 50) + (c if x > 50 else 0.0)  # noqa: E731
    jump = arms.fitted_jump(LEFT_X, [f(x) for x in LEFT_X], RIGHT_X, [f(x) for x in RIGHT_X], 50.0)
    assert close(jump, c, abs(a) + abs(b) + abs(c))


@given(ys=st.lists(num, min_size=6, max_size=6), zs=st.lists(num, min_size=6, max_size=6), k=num)
def test_mock_the_jump_is_linear_in_the_answers(ys, zs, k):
    j = lambda v: arms.fitted_jump(LEFT_X, v[:3], RIGHT_X, v[3:], 50.0)  # noqa: E731
    combo = [y + k * z for y, z in zip(ys, zs)]
    assert close(j(combo), j(ys) + k * j(zs), sum(map(abs, combo)) + sum(map(abs, ys)) + abs(k) * sum(map(abs, zs)))


@given(ys=st.lists(num, min_size=6, max_size=6))
def test_mock_the_jump_matches_its_closed_form(ys):
    """Differential: half-point ranges put the left line's value at 50% at -5/12, 1/3, 13/12 of its
    three answers, and the right line's at 13/12, 1/3, -5/12."""
    y1, y2, y3, y4, y5, y6 = ys
    closed = (13 / 12 * y4 + y5 / 3 - 5 / 12 * y6) - (-5 / 12 * y1 + y2 / 3 + 13 / 12 * y3)
    assert close(arms.fitted_jump(LEFT_X, ys[:3], RIGHT_X, ys[3:], 50.0), closed, sum(map(abs, ys)))


def test_the_fit_reproduces_the_texas_studys_published_jumps():
    """Differential: on the Texas study's committed table (outputs/rd.csv at cb2d06b of
    texas-governor-data-center-forecasts, cells 44, 47, 49.5 | 50.5, 53, 56), this module's fit gives
    the same fitted jumps and cutoff contrasts."""
    rows = list(csv.DictReader(open(FIXTURES / "texas_rd.csv")))
    assert len(rows) == 132
    for r in rows:
        y = [float(r[f"at_{c}"]) for c in ("44", "47", "49.5", "50.5", "53", "56")]
        fit = arms.fitted_jump([44, 47, 49.5], y[:3], [50.5, 53, 56], y[3:], 50.0)
        assert close(fit, float(r["fit_jump"]), max(map(abs, y))), r
        assert close(y[3] - y[2], float(r["jump"]), max(map(abs, y))), r


# --- the map's range cells -------------------------------------------------------------------------

def alaska() -> dict:
    return yaml.safe_load(GRAPH_PATH.read_text())


def load_raw(raw: dict, tmp_path) -> graph_mod.Graph:
    path = tmp_path / "graph.yaml"
    path.write_text(yaml.safe_dump(raw))
    return graph_mod.load(path)


def test_the_maps_ranges_tile_the_shares(tx_graph):
    g = tx_graph
    assert [g.label(c) for c in g.cells] == ["dem_to_48.5", "dem_48.5_49.0", "dem_49.0_49.5", "dem_49.5_50.0",
                                             "dem_50.0_50.5", "dem_50.5_51.0", "dem_51.0_51.5", "dem_over_51.5"]
    assert g.ranges[g.cells[0]][0] is None and g.ranges[g.cells[-1]][1] is None
    assert [g.ranges[c][1] for c in g.cells[:-1]] == [g.ranges[c][0] for c in g.cells[1:]]
    assert len(g.closed("fail")) == len(g.closed("pass")) == 3
    assert [round(arms.midpoint_pct(g, c), 9) for c in g.closed("fail") + g.closed("pass")] == LEFT_X + RIGHT_X
    assert set(g.share_grid) == {hi for _, hi in g.ranges.values() if hi is not None}


def _open_below(c):
    """MOCK: the lowest Peltola range loses its lower end. (A Sullivan range above the lowest can't get
    this far: losing its lower end leaves a gap below it, which the meeting check refuses first.)"""
    c["pass"][0].pop("lo")
    c["pass"][0]["at"] = 0.5025


def _open_above(c):
    """MOCK: the top range closes and the one below it loses its upper end."""
    c["pass"][3].update(lo=0.515, hi=0.53, at=0.52)
    c["pass"][2].pop("hi")
    c["pass"][2]["at"] = 0.5125


@pytest.mark.parametrize("change, message", [
    (lambda c: c["fail"][1].update(lo=0.484), "must meet"),
    (lambda c: c["pass"][0].update(lo=0.501), "must start at 0.5"),
    (lambda c: c["fail"][3].update(hi=0.499), "must end at 0.5"),
    (_open_below, "only the lowest fail range may be open below"),
    (_open_above, "only the highest pass range may be open above"),
    (lambda c: c["fail"][1].pop("lo"), "needs `at`"),
    (lambda c: c["pass"][1].update(label="dem_50.0_50.5"), "distinct labels"),
    (lambda c: c["pass"][1].update(label="dem 50"), "label must be"),
    (lambda c: c["fail"][0].pop("at"), "needs `at`"),
    (lambda c: c["fail"].append(0.47), "all numbers"),
    (lambda c: c["pass"][2].update(colour="red"), "only label"),
])
def test_mock_a_bad_range_map_is_refused(tmp_path, change, message):
    raw = alaska()
    change(raw["cells"])
    with pytest.raises(GraphError, match=message):
        load_raw(raw, tmp_path)


@settings(max_examples=40, deadline=None)
@given(left=st.lists(st.integers(1, 99), min_size=1, max_size=5, unique=True),
       right=st.lists(st.integers(1, 99), min_size=1, max_size=5, unique=True),
       open_low=st.booleans(), open_high=st.booleans())
def test_mock_any_tiling_loads_and_keys_entries_by_label(tmp_path_factory, left, right, open_low, open_high):
    """Random edges in (0.40, 0.50) and (0.50, 0.60), with 0.49 and 0.51 always among them, make a
    tiling; each loads with those edges as its share grid, and every ranged question's entries are its
    labels, lowest first."""
    lo_edges = sorted({round(0.40 + e / 1000, 3) for e in left} | {0.49})
    hi_edges = sorted({round(0.50 + e / 1000, 3) for e in right} | {0.51})
    fail_edges, pass_edges = [*lo_edges, 0.5], [0.5, *hi_edges]
    fail = [{"label": f"f{i}", "lo": a, "hi": b} for i, (a, b) in enumerate(zip(fail_edges, fail_edges[1:]))]
    pas = [{"label": f"p{i}", "lo": a, "hi": b} for i, (a, b) in enumerate(zip(pass_edges, pass_edges[1:]))]
    if open_low:
        fail.insert(0, {"label": "f_low", "hi": fail_edges[0], "at": fail_edges[0] - 0.001})
    if open_high:
        pas.append({"label": "p_high", "lo": pass_edges[-1], "at": pass_edges[-1] + 0.001})
    raw = alaska()
    raw["cells"] = {"fail": fail, "pass": pas}
    raw["share_grid"] = [*lo_edges, 0.5, *hi_edges]
    g = load_raw(raw, tmp_path_factory.mktemp("tiling"))
    labels = [c["label"] for c in fail + pas]
    assert [g.label(c) for c in g.cells] == labels
    node = g.nodes["recount"]
    assert [e.key for e in g.entries(node)] == labels
    assert list(template(g)["nodes"]["recount"]["forecasts"]) == labels


@pytest.mark.parametrize("grid", [
    [0.485, 0.49, 0.495, 0.5, 0.505, 0.51],  # an end missing
    [0.485, 0.49, 0.495, 0.5, 0.505, 0.51, 0.515, 0.52],  # a point no range ends at
])
def test_mock_a_share_grid_off_the_range_ends_is_refused(tmp_path, grid):
    raw = alaska()
    raw["share_grid"] = grid
    with pytest.raises(GraphError, match="exactly the range cells' finite ends"):
        load_raw(raw, tmp_path)


# --- the briefs --------------------------------------------------------------------------------------

def test_the_arms_differ_only_by_their_blocks(tx_graph):
    g = tx_graph
    base = prompt.build(g, "00")
    note = f"- Note: {prompt.PLACEBO_NOTE}\n"
    for arm in prompt.ARMS:
        text = prompt.build(g, arm)
        estimand, placebo = arm[0] == "1", arm[1] == "1"
        assert (prompt.ESTIMAND in text) is estimand
        assert (prompt.PLACEBO_RULE in text) is placebo and (note in text) is placebo
        stripped = text.replace(prompt.ESTIMAND, ".") if estimand else text
        stripped = stripped.replace(prompt.PLACEBO_RULE, "").replace(note, "")
        if estimand and placebo:
            stripped = stripped.replace("..", ".")
        assert stripped == base, arm
    assert "the difference between them is your estimate of what a bare win changes" in prompt.ESTIMAND
    assert "cannot change its result" in prompt.PLACEBO_RULE
    assert "Brier score" in base and "void and are" in base and "Ballot Measure 2" in base


def test_mock_a_completed_template_parses_in_every_arm(tx_graph):
    obj = mock_response(tx_graph, lambda node, x: 0.5)
    fc = parse(tx_graph, obj)
    assert set(fc.values["recount"]) == {tx_graph.label(c) for c in tx_graph.cells}


# --- the assignment ----------------------------------------------------------------------------------

def test_the_assignment_is_balanced_seeded_and_alternating():
    plan = cli.assignment()
    runs = plan["runs"]
    assert plan == cli.assignment(cli.SEED) and plan != cli.assignment(cli.SEED + 1)
    assert len(runs) == 24 and len({r["label"] for r in runs}) == 24
    assert [r["order"] for r in runs] == list(range(1, 25))
    for fam in cli.FAMILIES:
        for arm in prompt.ARMS:
            assert sum(r["family"] == fam and r["arm"] == arm for r in runs) == cli.PER_ARM
    assert all(a["family"] != b["family"] for a, b in zip(runs, runs[1:]))
    assert {(r["family"], r["tier"], r["model"]) for r in runs} == {("opus", "standard", "opus"), ("sol", "hard", None)}


def test_the_committed_assignment_matches_its_seed():
    committed = json.loads(cli.ASSIGNMENT.read_text())
    assert committed == cli.assignment(committed["seed"])


def test_mock_a_hand_edited_assignment_is_refused(tmp_path, monkeypatch):
    """Swapping two runs' arms within a family keeps the balance but breaks the seed's assignment."""
    plan = cli.assignment()
    runs = plan["runs"]
    i, j = next((i, j) for i in range(len(runs)) for j in range(i + 1, len(runs))
                if runs[i]["family"] == runs[j]["family"] and runs[i]["arm"] != runs[j]["arm"])
    runs[i]["arm"], runs[j]["arm"] = runs[j]["arm"], runs[i]["arm"]
    path = tmp_path / "assignment.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(cli, "ASSIGNMENT", path)
    with pytest.raises(SystemExit, match="never edit it by hand"):
        cli.planned(runs[i]["label"])


def test_mock_dispatch_refuses_the_wrong_family_settings(monkeypatch):
    import argparse

    row = next(r for r in cli.assignment()["runs"] if r["family"] == "sol")
    args = argparse.Namespace(backend="subfleet", label=row["label"], tier="standard", model="opus", late=False)
    with pytest.raises(SystemExit, match="--tier hard and no -m"):
        cli.cmd_dispatch(args)


# --- the analysis on MOCK runs -------------------------------------------------------------------------

def mock_response(g, value) -> dict:
    """A MOCK filled template: value(node, x) at each range's representative share x, in percent."""
    obj = template(g)
    obj["forecaster"].update(model="MOCK", prior_context="none", notes="MOCK")
    obj["research_summary"] = "MOCK"
    for node in g.forecast_nodes():
        item = obj["nodes"][node.id]
        item["reasoning"] = "MOCK"
        if node.baseline:
            item["baseline"] = {"value": 1, "as_of": "MOCK", "source": "MOCK"}
        if node.kind == "share_cdf":
            item["forecasts"]["all"] = {k: (i + 1) / (len(g.share_grid) + 1)
                                        for i, k in enumerate(item["forecasts"]["all"])}
            continue
        for c in g.cells:
            v = value(node, c * 100)
            if node.kind == "binary":
                item["forecasts"][g.label(c)] = {"p": v}
            else:
                lo, hi = node.bounds
                v = min(max(lo + (hi - lo) * v, lo), hi)
                item["forecasts"][g.label(c)] = {quantile_key(q): v for q in g.quantiles}
    return obj


@settings(max_examples=60, deadline=None)
@given(a=st.floats(0.3, 0.5), b=st.floats(-0.05, 0.05), c=st.floats(-0.2, 0.2))
def test_mock_run_measures_recover_a_step_and_a_slope(tx_graph, a, b, c):
    """Answers a + b(x - 50) + c[x > 50] at the ranges' midpoints give jump c, slope b and contrast
    c + b/2 for every yes/no question, and the same scaled by the bounds' width for quantities."""
    g = tx_graph
    mids = {c_: arms.midpoint_pct(g, c_) if None not in g.ranges[c_] else c_ * 100 for c_ in g.cells}

    def value(node, x):
        cell = min(g.cells, key=lambda c_: abs(c_ * 100 - x))
        m = mids[cell]
        return a + b * (m - 50) + (c if m > 50 else 0.0)

    fc = parse(g, mock_response(g, value))
    out = arms.run_measures(g, fc)
    for node in arms.ranged_nodes(g):
        width = 1.0 if node.kind == "binary" else node.bounds[1] - node.bounds[0]
        m = out[node.id]
        assert close(m["jump"], c * width, width)
        assert close(m["slope"], b * width, width)
        assert close(m["contrast"], (c + b * 0.5) * width, width)
        assert close(m["abs_jump"], abs(c) * width, width)


def mock_rows(per_cell, f):
    """MOCK rows: per_cell runs in each family-by-arm cell, with value f(family, arm, i)."""
    return [(fam, arm, f(fam, arm, i)) for fam in arms.FAMILIES for arm in arms.ARMS for i in range(per_cell)]


@given(base_o=num, base_s=num, d=num, g_=num, noise=st.lists(num, min_size=3, max_size=3))
def test_mock_main_effects_recover_additive_blocks(base_o, base_s, d, g_, noise):
    """With each run at base_family + d[estimand] + g[placebo] + noise_i (the same noise in every
    cell), the estimand effect is d, the placebo effect g and the interaction 0."""
    base = {"opus": base_o, "sol": base_s}
    rows = mock_rows(3, lambda fam, arm, i: base[fam] + d * (arm[0] == "1") + g_ * (arm[1] == "1") + noise[i])
    scale = abs(base_o) + abs(base_s) + abs(d) + abs(g_) + sum(map(abs, noise))
    assert close(arms.effect(rows, arms.MAIN_E).est, d, scale)
    assert close(arms.effect(rows, arms.MAIN_P).est, g_, scale)
    assert close(arms.effect(rows, arms.INTERACTION).est, 0.0, scale)


@given(xs=st.lists(num, min_size=2, max_size=8), ys=st.lists(num, min_size=2, max_size=8))
def test_mock_a_two_group_difference_is_welchs_t(xs, ys):
    """Differential: a difference of two means gets Welch's statistic and degrees of freedom. (scipy's
    own formula squares each variance unscaled, so it underflows where these don't; those inputs are
    skipped.)"""
    assume(len(set(xs)) > 1 and len(set(ys)) > 1)
    import statistics
    import sys
    assume(all((statistics.variance(v) / len(v)) ** 2 > sys.float_info.min for v in (xs, ys)))
    e = arms.combination([(1.0, xs, "x"), (-1.0, ys, "y")])
    ref = stats.ttest_ind(xs, ys, equal_var=False)
    assume(math.isfinite(ref.statistic) and abs(ref.statistic) < 1e6)
    assert math.isclose(e.t, ref.statistic, rel_tol=1e-6, abs_tol=1e-9)
    assert math.isclose(e.df, ref.df, rel_tol=1e-6)


@given(vals=st.lists(num, min_size=2, max_size=10))
def test_mock_one_group_is_its_mean_with_its_standard_error(vals):
    e = arms.combination([(1.0, vals, "x")])
    assert close(e.est, sum(vals) / len(vals), sum(map(abs, vals)))
    assert e.df == pytest.approx(len(vals) - 1) or e.se == 0
    assert e.se >= 0


def test_mock_tiny_variances_keep_their_degrees_of_freedom():
    """Two groups of two with equal, tiny variances: Welch-Satterthwaite gives 2, which squaring the
    unscaled variances would lose to underflow."""
    vals = [0.0, 6.7e-102]
    e = arms.combination([(1.0, vals, "x"), (-1.0, vals, "y")])
    assert e.df == pytest.approx(2.0)
    flat = arms.combination([(1.0, [3.0, 3.0], "x"), (-1.0, [1.0, 1.0], "y")])
    assert (flat.est, flat.se) == (2.0, 0.0) and math.isnan(flat.df)


def test_mock_families_count_equally_however_many_runs_each_has():
    rows = [("opus", "10", 1.0)] * 5 + [("sol", "10", 3.0)]
    assert arms.arm_mean(rows, "10").est == pytest.approx(2.0)
    assert math.isnan(arms.arm_mean(rows, "10").se)  # one Sol run: no variance
    assert math.isnan(arms.arm_mean(rows, "00").est)  # no runs at all


def test_mock_randomization_p_is_one_without_differences_and_small_with_a_clean_split():
    flat = mock_rows(3, lambda fam, arm, i: 7.0)
    assert arms.randomization_p(flat, arms.MAIN_E, draws=500, seed=1) == 1.0
    split = mock_rows(3, lambda fam, arm, i: 10.0 * (arm[0] == "1") + 0.01 * i)
    assert arms.randomization_p(split, arms.MAIN_E, draws=2000, seed=1) < 0.01
    assert arms.randomization_p(split, arms.MAIN_P, draws=2000, seed=1) > 0.05


@settings(max_examples=25, deadline=None)
@given(vals=st.lists(st.floats(-10, 10), min_size=24, max_size=24), seed=st.integers(0, 10**6))
def test_mock_randomization_p_is_a_valid_p_value(vals, seed):
    rows = [(fam, arm, vals[k]) for k, (fam, arm) in
            enumerate((f, a) for f in arms.FAMILIES for a in arms.ARMS for _ in range(3))]
    p = arms.randomization_p(rows, arms.MAIN_P, draws=200, seed=seed)
    assert 1 / 201 <= p <= 1.0


def test_mock_the_primary_index_averages_its_questions():
    m = {n: {"abs_jump": float(i)} for i, n in enumerate(arms.EFFECT_INDEX)}
    assert arms.index(m, arms.EFFECT_INDEX, "abs_jump") == pytest.approx(2.0)
    assert [p[0] for p in arms.PRIMARY] == ["P1 coached placebo", "P2 uncoached placebo", "E1 effect index"]
    assert arms.PLACEBO_COACHED == prompt.PLACEBO_NODE


def test_mock_accepted_runs_checks_each_runs_own_arm(tmp_path, monkeypatch, tx_graph):
    from i86 import runners

    monkeypatch.setattr(runners, "RUNS", tmp_path / "runs")
    obj = mock_response(tx_graph, lambda node, x: 0.5)
    for label, arm, sha in [("opus-01", "10", "sha10"), ("sol-01", "01", "stale")]:
        d = tmp_path / "runs" / label
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"label": label, "arm": arm, "prompt_sha256": sha,
                                                 "backend": "manual"}))
        (d / "response.json").write_text(json.dumps(copy.deepcopy(obj)))
    shas = {"00": "sha00", "01": "sha01", "10": "sha10", "11": "sha11"}
    assert set(cli.accepted_runs(tx_graph, shas, allow_unaudited=True)) == {"opus-01"}


@given(ps=st.lists(st.floats(0, 1), min_size=1, max_size=6))
def test_mock_holm_matches_its_definition_and_never_lowers_a_p(ps):
    """Differential: each adjusted p is the largest, over the p-values at or below it in the order,
    of (m - rank + 1) * p, capped at 1."""
    named = {f"h{i}": p for i, p in enumerate(ps)}
    adj = arms.holm(named)
    order = sorted(named, key=lambda k: named[k])
    m = len(ps)
    for i, k in enumerate(order):
        assert adj[k] == pytest.approx(max(min(1.0, (m - j) * named[order[j]]) for j in range(i + 1)))
        assert named[k] <= adj[k] + 1e-15 and adj[k] <= 1.0
    assert arms.holm({"a": 0.01, "b": 0.04, "c": 0.03}) == pytest.approx({"a": 0.03, "b": 0.06, "c": 0.06})


def test_mock_family_effects_recover_each_familys_own_effect():
    """MOCK: the placebo block lowers Opus's values by 2 and Sol's by 6; the registered effect is their
    mean, 4."""
    drop = {"opus": 2.0, "sol": 6.0}
    rows = mock_rows(3, lambda fam, arm, i: 10.0 - drop[fam] * (arm[1] == "1") + 0.1 * i)
    assert arms.family_effect(rows, arms.MAIN_P, "opus").est == pytest.approx(-2.0)
    assert arms.family_effect(rows, arms.MAIN_P, "sol").est == pytest.approx(-6.0)
    assert arms.effect(rows, arms.MAIN_P).est == pytest.approx(-4.0)


def test_mock_a_run_another_model_served_stays_out(tmp_path, monkeypatch, tx_graph):
    """MOCK: an Opus run that Subfleet records as served by another model, or with no served model,
    stays out; a Sol run served by GPT-6.1 Sol stays in."""
    from i86 import runners

    monkeypatch.setattr(runners, "RUNS", tmp_path / "runs")
    obj = mock_response(tx_graph, lambda node, x: 0.5)
    for label, family, served in [("opus-01", "opus", "claude-opus-4-8"), ("opus-02", "opus", None),
                                  ("sol-01", "sol", "gpt-6.1-sol"), ("opus-03", "opus", "claude-opus-5-5")]:
        d = tmp_path / "runs" / label
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"label": label, "arm": "00", "family": family,
                                                 "prompt_sha256": "sha00", "backend": "manual",
                                                 **({"model_served": served} if served else {})}))
        (d / "response.json").write_text(json.dumps(copy.deepcopy(obj)))
        if family == "sol":
            (d / "codex-sessions").mkdir()
            (d / "codex-sessions" / "audit.json").write_text(json.dumps({"passed": True, "violations": [], "threads": [{"audit": {"suspect": []}}]}))
    assert set(cli.accepted_runs(tx_graph, {"00": "sha00"}, allow_unaudited=True)) == {"sol-01", "opus-03"}
