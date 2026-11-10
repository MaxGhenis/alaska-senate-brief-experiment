"""scripts/power.py: post hoc power for the registered primaries. Every run and value here is MOCK
data.

Invariants:
- `holm_rows` equals arms.holm on every row (property test over generated p-values, ties included);
- `randomization` gives arms.effect's estimate exactly and arms.randomization_p's p-value within Monte
  Carlo error, on unbalanced MOCK studies (differential test);
- `balanced_var` gives the variance arms.effect reports for a balanced MOCK study (differential test),
  which is 2 (s_opus^2 + s_sol^2) / n for a main effect;
- `analytic_power` equals alpha with no effect and never falls as the effect or the study grows
  (property test); `runs_needed` returns the smallest multiple of 8 that reaches the target power, and
  `mde` the effect that reaches it;
- `second_wave_power` with an uninformative first wave equals the second wave's own normal power
  (differential test), and reduces to the first wave's own test as the second wave shrinks;
- the simulated estimates vary as the analytic standard error says; with no effect the simulated rule
  rejects any primary at most 5% of the time; and the simulated power agrees with the analytic
  approximation on normal MOCK runs (differential test);
- a study whose effects arrive as a dict equals the same study with one target (differential test), and
  `simulated_runs_needed` returns a multiple of 8 at which the simulated rule reaches its power;
- main() refuses a per-run table that disagrees with its effects.csv and writes its outputs, the joint
  file among them when every primary's count fits under --simulate-max, and marks an interval end that
  no absolute jump can reach;
- `oriented_index` with a reversed answer read the other way round equals the absolute index when
  every other answer runs the usual way (differential test), and main() quotes each reversed answer's
  own reasoning from its sealed response.json.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy import stats

from i86 import arms

spec = importlib.util.spec_from_file_location("power", Path(__file__).parent.parent / "scripts" / "power.py")
power = importlib.util.module_from_spec(spec)
spec.loader.exec_module(power)

NAMES = [name for name, _, _, _ in arms.PRIMARY]
OBSERVED_COUNTS = {"opus": (3, 3, 3, 2), "sol": (3, 2, 3, 2)}  # MOCK: the registered analysis's cell sizes


def mock_rows(counts: dict[str, tuple[int, ...]], sd: dict[str, float], shift: float = 0.0,
              contrast=arms.MAIN_E, seed: int = 0) -> list[tuple[str, str, float]]:
    """MOCK (family, arm, value) rows: normal noise about a family mean, plus `shift` times the
    contrast's weight."""
    rng = np.random.default_rng(seed)
    rows = []
    for fam, ns in counts.items():
        base = 1.0 if fam == "opus" else 2.0
        for arm, n in zip(arms.ARMS, ns):
            rows += [(fam, arm, float(base + shift * contrast[arm] + rng.normal(0, sd[fam]))) for _ in range(n)]
    return rows


def mock_primaries(counts, sd, seed=0) -> dict[str, list[tuple[str, str, float]]]:
    return {name: mock_rows(counts, sd, seed=seed + i) for i, name in enumerate(NAMES)}


# ---------------------------------------------------------------------------- Holm


@settings(max_examples=300, deadline=None)
@given(st.lists(st.lists(st.one_of(st.sampled_from([0.001, 0.01, 0.02, 0.05, 1.0]),
                                   st.floats(min_value=1e-6, max_value=1.0)), min_size=3, max_size=3),
                min_size=1, max_size=20))
def test_mock_holm_rows_matches_the_registered_holm(ps):
    p = np.array(ps)
    out = power.holm_rows(p)
    for row, adj in zip(p, out):
        ref = arms.holm({n: float(x) for n, x in zip(NAMES, row)})
        assert adj == pytest.approx([ref[n] for n in NAMES], abs=1e-12)


# ---------------------------------------------------------------------------- the registered estimate and test


@pytest.mark.parametrize("contrast", [arms.MAIN_E, arms.MAIN_P, arms.INTERACTION])
def test_mock_randomization_matches_the_registered_estimate_and_p(contrast):
    rows = mock_rows(OBSERVED_COUNTS, {"opus": 0.3, "sol": 0.5}, shift=0.3, contrast=contrast, seed=3)
    ys, idx = [], []
    for fam in arms.FAMILIES:
        ys.append(np.array([[[v] for f, _, v in rows if f == fam]]))
        idx.append(np.array([arms.ARMS.index(a) for f, a, _ in rows if f == fam]))
    weights = np.array([[contrast[a] for a in arms.ARMS]])
    draws = 20_000
    est, p = power.randomization(ys, idx, weights, draws, np.random.default_rng(1))
    assert est[0, 0] == pytest.approx(arms.effect(rows, contrast).est, abs=1e-12)
    ref = arms.randomization_p(rows, contrast, draws=draws, seed=0)
    assert abs(p[0, 0] - ref) <= 4 * math.sqrt(2 * ref * (1 - ref) / draws) + 1e-3


def test_mock_balanced_var_is_the_registered_variance():
    rows = mock_rows({"opus": (3, 3, 3, 3), "sol": (3, 3, 3, 3)}, {"opus": 0.4, "sol": 0.9}, seed=5)
    for contrast in (arms.MAIN_E, arms.MAIN_P):
        s2 = power.family_variances(rows)
        assert sum(power.balanced_var(s2, 24, contrast).values()) == pytest.approx(
            arms.effect(rows, contrast).se ** 2, rel=1e-12)


@given(st.floats(1e-4, 10), st.floats(1e-4, 10), st.integers(3, 500).map(lambda k: 8 * k))
def test_mock_balanced_var_is_two_s2_over_n(a, b, n):
    total = sum(power.balanced_var({"opus": a, "sol": b}, n, arms.MAIN_P).values())
    assert total == pytest.approx(2 * (a + b) / n, rel=1e-12)


# ---------------------------------------------------------------------------- analytic


variances = st.fixed_dictionaries({"opus": st.floats(1e-4, 10), "sol": st.floats(1e-4, 10)})
sizes = st.integers(3, 2000).map(lambda k: 8 * k)


@settings(max_examples=200, deadline=None)
@given(variances, sizes)
def test_mock_no_effect_gives_power_alpha(s2, n):
    assert power.analytic_power(0.0, s2, n, arms.MAIN_E) == pytest.approx(power.ALPHA / 3, abs=1e-9)


@settings(max_examples=200, deadline=None)
@given(variances, sizes, st.floats(0.0, 5.0), st.floats(1.0, 3.0))
def test_mock_power_never_falls_as_the_effect_or_study_grows(s2, n, delta, k):
    p = power.analytic_power(delta, s2, n, arms.MAIN_E)
    assert power.analytic_power(delta, s2, n + 8, arms.MAIN_E) >= p - 1e-12
    assert power.analytic_power(delta * k, s2, n, arms.MAIN_E) >= p - 1e-12
    assert power.analytic_power(-delta, s2, n, arms.MAIN_E) == pytest.approx(p, abs=1e-12)


@settings(max_examples=100, deadline=None)
@given(variances, st.floats(0.05, 3.0))
def test_mock_runs_needed_is_the_smallest_multiple_of_8_that_reaches_80_percent(s2, z):
    delta = z * math.sqrt(sum(s2.values()))  # keeps the answer in a range a test can walk
    n = power.runs_needed(delta, s2, arms.MAIN_E)
    assert n % 8 == 0
    assert power.analytic_power(delta, s2, n, arms.MAIN_E) >= power.POWER
    assert n - 8 < 16 or power.analytic_power(delta, s2, n - 8, arms.MAIN_E) < power.POWER


@settings(max_examples=100, deadline=None)
@given(variances, sizes)
def test_mock_mde_reaches_80_percent(s2, n):
    assert power.analytic_power(power.mde(s2, n, arms.MAIN_E), s2, n, arms.MAIN_E) == pytest.approx(power.POWER, abs=1e-6)


def test_mock_mde_from_se_matches_mde_for_the_same_standard_error():
    s2, n = {"opus": 0.5, "sol": 1.5}, 48
    se = math.sqrt(sum(power.balanced_var(s2, n, arms.MAIN_E).values()))
    parts = power.balanced_var(s2, n, arms.MAIN_E)  # Welch df, each family's df being its runs less 4
    df = sum(parts.values()) ** 2 / sum(v ** 2 / (n / 2 - 4) for v in parts.values())
    assert power.mde_from_se(se, df) == pytest.approx(power.mde(s2, n, arms.MAIN_E), rel=1e-6)


def test_mock_the_registered_design_matches_the_registrations_power_statement():
    """README's 'Power and limits': 12 runs against 12 detect about 1.22 run-level standard deviations at
    5% and 1.45 at 5%/3, 80% of the time."""
    s2 = {"opus": 1.0, "sol": 1.0}
    assert power.mde(s2, 24, arms.MAIN_E, alpha=0.05) == pytest.approx(1.22, abs=0.01)
    assert power.mde(s2, 24, arms.MAIN_E) == pytest.approx(1.45, abs=0.01)


# ---------------------------------------------------------------------------- a second wave


@settings(max_examples=100, deadline=None)
@given(variances, st.integers(1, 500).map(lambda k: 8 * k), st.floats(-3, 3))
def test_mock_an_uninformative_first_wave_leaves_the_second_waves_own_power(s2, n2, z):
    se2 = math.sqrt(sum(power.balanced_var(s2, n2, arms.MAIN_E).values()))
    delta = z * se2
    crit = stats.norm.ppf(1 - power.ALPHA / 6)
    own = stats.norm.sf(crit - delta / se2) + stats.norm.cdf(-crit - delta / se2)
    got = power.second_wave_power(0.7, 1e9, delta, s2, n2, arms.MAIN_E)
    assert got == pytest.approx(own, abs=1e-6)


@pytest.mark.parametrize("z1", [0.5, 1.5, 3.0])
def test_mock_a_vanishing_second_wave_leaves_the_first_waves_own_test(z1):
    s2 = {"opus": 1.0, "sol": 1.0}
    crit = stats.norm.ppf(1 - power.ALPHA / 6)
    tiny = power.second_wave_power(z1, 1.0, z1, {f: v * 1e12 for f, v in s2.items()}, 8, arms.MAIN_E)
    assert tiny == pytest.approx(1.0 if z1 > crit else 0.0, abs=1e-6)


# ---------------------------------------------------------------------------- simulation


def test_mock_simulated_estimates_vary_as_the_analytic_standard_error_says():
    prim = mock_primaries(OBSERVED_COUNTS, {"opus": 0.3, "sol": 0.6}, seed=11)
    sim = power.Simulator(prim, seed=2)
    ys, arm = sim.draw(48, None, 0.0, 6000)
    est, _ = power.randomization(ys, [arm] * len(ys), sim.weights, 2, sim.rng)
    for j, name in enumerate(sim.names):
        se = math.sqrt(sum(power.balanced_var(power.family_variances(prim[name]), 48, power.contrast_of(name)).values()))
        assert est[:, j].std() == pytest.approx(se, rel=0.05)


@pytest.mark.parametrize("n", [24, 48])
def test_mock_with_no_effect_the_rule_rejects_at_most_5_percent(n):
    sim = power.Simulator(mock_primaries(OBSERVED_COUNTS, {"opus": 0.3, "sol": 0.6}, seed=13), seed=4)
    studies = 3000
    rej = sim.reject(n, None, 0.0, studies, 1000)
    assert rej.any(axis=1).mean() <= power.ALPHA + 3 * math.sqrt(power.ALPHA * (1 - power.ALPHA) / studies)


def test_mock_simulated_power_agrees_with_the_analytic_approximation():
    counts = {"opus": (10, 10, 10, 10), "sol": (10, 10, 10, 10)}  # MOCK: a pool big enough to look normal
    prim = mock_primaries(counts, {"opus": 0.5, "sol": 0.5}, seed=17)
    sim = power.Simulator(prim, seed=6)
    name = "E1 effect index"
    s2 = power.family_variances(prim[name])
    delta = power.mde(s2, 48, arms.MAIN_E)  # analytic power 0.8
    got = sim.power(48, name, delta, 2000, 1000)[sim.names.index(name)]
    # Holm adds a little power over 5%/3 when another primary falsely rejects first; Monte Carlo adds noise.
    assert 0.72 <= got <= 0.9


# ---------------------------------------------------------------------------- main


def write_mock_tables(d: Path, primaries: dict[str, list[tuple[str, str, float]]], tamper: float = 0.0,
                      ci: tuple[float, float] | None = None) -> Path:
    """A MOCK per_run.csv in scripts/arms.py's layout (each primary's questions carry its value) and the
    effects.csv rows for the three primaries, from arms.effect, with MOCK interval ends `ci` if given."""
    d.mkdir(parents=True, exist_ok=True)
    questions = {name: nodes for name, nodes, _, _ in arms.PRIMARY}
    first = primaries[NAMES[0]]
    with open(d / "per_run.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run", "family", "arm", "question", "abs_jump"])
        for i, (fam, arm, _) in enumerate(first):
            for name in NAMES:
                for q in questions[name]:
                    w.writerow([f"{fam}-MOCK{i:02d}", fam, arm, q, primaries[name][i][2]])
    with open(d / "effects.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["outcome", "estimate", "se", "randomization_p", "holm_p", "registered_primary"]
                   + (["ci_lo", "ci_hi"] if ci else []))
        for name in NAMES:
            e = arms.effect(primaries[name], power.contrast_of(name))
            w.writerow([name, e.est + tamper, e.se, 0.5, 1.0, "True", *(ci or ())])
    return d / "per_run.csv"


def test_mock_main_writes_its_outputs(tmp_path):
    per_run = write_mock_tables(tmp_path / "in", mock_primaries(OBSERVED_COUNTS, {"opus": 0.3, "sol": 0.6}, seed=19))
    out = tmp_path / "out"
    assert power.main(["--per-run", str(per_run), "--out", str(out), "--studies", "40", "--draws", "40",
                       "--sizes", "24", "48", "--waves", "24", "--simulate-max", "48"]) == 0
    for name in ("power.csv", "size.csv", "second_wave.csv", "summary.md"):
        assert (out / name).exists(), name
    text = (out / "summary.md").read_text()
    assert "Exploratory and post hoc" in text and "not observations" in text
    rows = list(csv.DictReader(open(out / "power.csv")))
    assert {r["primary"] for r in rows} == set(NAMES)
    assert not any(r["effect"] != "today's estimate" for r in rows if r["primary"] == "P1 coached placebo")


def test_mock_a_dict_of_effects_draws_the_same_study_as_one_target():
    primaries = mock_primaries(OBSERVED_COUNTS, {"opus": 0.3, "sol": 0.6}, seed=31)
    one = power.Simulator(primaries, seed=5).draw(24, NAMES[1], 0.7, 6)
    many = power.Simulator(primaries, seed=5).draw(24, {NAMES[1]: 0.7}, 0.0, 6)
    for a, b in zip(one[0], many[0]):
        np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(one[1], many[1])
    both = power.Simulator(primaries, seed=5).draw(24, {NAMES[0]: 0.2, NAMES[1]: 0.7}, 0.0, 6)
    w = np.array([power.contrast_of(NAMES[0])[arms.ARMS[j]] for j in both[1]])
    for a, b in zip(many[0], both[0]):  # the second effect moves the first primary alone, by its contrast's weights
        np.testing.assert_allclose(b[:, :, 0] - a[:, :, 0], 0.2 * w + 0 * a[:, :, 0], atol=1e-12)
        np.testing.assert_array_equal(a[:, :, 1:], b[:, :, 1:])


def test_mock_simulated_runs_needed_finds_a_size_that_reaches_its_power():
    counts = {"opus": (10, 10, 10, 10), "sol": (10, 10, 10, 10)}  # MOCK: a pool big enough to look normal
    primaries = {name: mock_rows(counts, {"opus": 1.0, "sol": 1.0}, seed=40 + i) for i, name in enumerate(NAMES)}
    sim = power.Simulator(primaries, seed=7)
    s2 = power.family_variances(primaries[NAMES[2]])
    effects = {name: 1.2 for name in NAMES}  # MOCK: every primary carries a real effect
    alone = power.runs_needed(1.2, s2, power.contrast_of(NAMES[2]))
    got = power.simulated_runs_needed(sim, effects, NAMES[2], alone, studies=400, draws=400)
    assert got % 8 == 0 and 16 <= got <= alone + 16, (got, alone)
    assert 0.7 <= sim.power(got, effects, 0.0, 600, 400)[2] <= 0.97
    assert power.simulated_runs_needed(sim, {NAMES[2]: 0.0}, NAMES[2], 16, studies=40, draws=40, cap=40) == math.inf


def test_mock_run_counts_name_the_searchs_floor():
    assert power.runs_words(16) == "16 or fewer" and power.runs_words(24) == "24"


def test_mock_main_writes_the_joint_file_and_marks_an_impossible_interval_end(tmp_path):
    # MOCK: each primary carries a large effect on its own contrast, so every count fits under --simulate-max.
    primaries = {name: mock_rows(OBSERVED_COUNTS, {"opus": 0.3, "sol": 0.6}, shift=1.5,
                                 contrast=power.contrast_of(name), seed=50 + i) for i, name in enumerate(NAMES)}
    per_run = write_mock_tables(tmp_path / "in", primaries, ci=(-1000.0, 2.0))
    out = tmp_path / "out"
    # 200 re-assignments: with fewer than 60, no randomization p-value can fall below 5%/3, so nothing rejects.
    assert power.main(["--per-run", str(per_run), "--out", str(out), "--studies", "60", "--draws", "200",
                       "--sizes", "24", "--waves", "24", "--simulate-max", "48"]) == 0
    rows = list(csv.DictReader(open(out / "joint.csv")))
    assert [r["primary"] for r in rows] == NAMES
    assert all(float(r["runs_needed_all_three_simulated"]) % 8 == 0 for r in rows), rows
    assert all(float(r["simulated_power_there_all_three"]) >= 0.8 for r in rows), rows
    text = (out / "summary.md").read_text()
    assert "If all three true effects equal today's estimates at once" in text
    assert "with the other two primaries' effects at zero" in text
    assert "| not possible |" in text and "16 or fewer" in text


def test_mock_main_refuses_a_per_run_table_that_disagrees_with_effects(tmp_path):
    per_run = write_mock_tables(tmp_path / "in", mock_primaries(OBSERVED_COUNTS, {"opus": 0.3, "sol": 0.6}, seed=23),
                                tamper=0.01)
    with pytest.raises(SystemExit, match="effects.csv says"):
        power.main(["--per-run", str(per_run), "--out", str(tmp_path / "out"), "--studies", "10", "--draws", "10",
                    "--sizes", "24", "--waves", "24", "--simulate-max", "24"])


# ---------------------------------------------------------------------------- what the counts rest on


@settings(max_examples=200, deadline=None)
@given(st.lists(st.floats(-100, 100), min_size=6, max_size=6))
def test_mock_jump_weights_are_the_registered_fitted_jump(ys):
    left, right = (48.75, 49.25, 49.75), (50.25, 50.75, 51.25)
    ref = arms.fitted_jump(left, ys[:3], right, ys[3:], 50.0)
    assert sum(w * y for w, y in zip(power.JUMP_WEIGHTS, ys)) == pytest.approx(ref, abs=1e-9)


@pytest.mark.parametrize("boundary", range(7))
def test_mock_a_step_scores_what_the_fitted_jump_makes_of_it(boundary):
    left, right = (48.75, 49.25, 49.75), (50.25, 50.75, 51.25)
    ys = [0.0] * boundary + [1.0] * (6 - boundary)  # MOCK: a step of 1 between closed ranges
    assert power.step_score(boundary) == pytest.approx(arms.fitted_jump(left, ys[:3], right, ys[3:], 50.0), abs=1e-12)
    assert power.step_score(boundary) == pytest.approx(power.step_score(6 - boundary) if boundary != 3 else 1.0)


@settings(max_examples=100, deadline=None)
@given(variances, st.integers(2, 500).map(lambda k: 8 * k), st.floats(-3, 3), st.floats(2.0, 60.0))
def test_mock_the_t_cutoff_never_exceeds_the_normal_one_and_meets_it_with_many_df(s2, n2, z1, df):
    se1 = 0.4
    args = (z1 * se1, se1, z1 * se1, s2, n2, arms.MAIN_E)
    normal = power.second_wave_power(*args)
    assert power.second_wave_power(*args, df_today=df) <= normal + 1e-12
    assert power.second_wave_power(*args, df_today=1e7) == pytest.approx(normal, abs=1e-4)


def test_mock_a_wave_of_one_run_per_cell_adds_no_degrees_of_freedom():
    s2 = {"opus": 1.0, "sol": 1.0}
    crit = stats.t.ppf(1 - power.ALPHA / 6, 13.0)
    got = power.second_wave_power(0.0, 1.0, 0.0, s2, 8, arms.MAIN_E, df_today=13.0)
    sd = math.sqrt((1 / sum(power.balanced_var(s2, 8, arms.MAIN_E).values()))
                   / (1 + 1 / sum(power.balanced_var(s2, 8, arms.MAIN_E).values())))
    assert got == pytest.approx(2 * stats.norm.sf(crit / sd), abs=1e-12)


@settings(max_examples=100, deadline=None)
@given(variances, st.integers(1, 40), st.integers(1, 40))
def test_mock_the_variance_range_brackets_today_and_narrows_with_df(s2, d1, d2):
    lo, hi = power.variance_range(s2, {"opus": d1, "sol": d2})
    assert 0 < lo < 1 < hi
    lo2, hi2 = power.variance_range(s2, {"opus": d1 * 100, "sol": d2 * 100})
    assert lo <= lo2 < 1 < hi2 <= hi


def test_mock_oriented_index_signs_a_reversed_answer_against_the_run(tmp_path):
    # MOCK: eight runs, every E1 question jumping +0.2, but one run answers sullivan_post at -0.2.
    nodes = next(n for name, n, _, _ in arms.PRIMARY if name == "E1 effect index")
    path = tmp_path / "per_run.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run", "family", "arm", "question", "jump", "abs_jump"])
        for i, (fam, arm) in enumerate((f, a) for f in arms.FAMILIES for a in arms.ARMS):
            for q in nodes:
                j = -0.2 if (i == 0 and q == "sullivan_post") else 0.2
                w.writerow([f"{fam}-MOCK{i}", fam, arm, q, j, abs(j)])
    oriented = power.oriented_index(path)
    assert sorted(v for _, _, v in oriented)[0] == pytest.approx((0.2 * (len(nodes) - 1) - 0.2) / len(nodes))
    assert power.reversed_answers(path) == [("opus-MOCK0", "sullivan_post", -0.2)]
    # Read the other way round, the reversed answer joins the rest, and the signed index equals the absolute one.
    back = power.oriented_index(path, swapped={("opus-MOCK0", "sullivan_post")})
    assert [v for _, _, v in back] == pytest.approx([0.2] * len(back))
    assert power.oriented_index(path, swapped={("opus-MOCK1", "no_such_question")}) == oriented


def test_mock_answer_reasoning_reads_the_sealed_answer(tmp_path):
    run = tmp_path / "sol-MOCK1"
    run.mkdir()
    (run / "response.json").write_text(json.dumps({"nodes": {
        "sullivan_post": {"forecasts": {}, "reasoning": "MOCK: losing\n  raises the chance."},
        "recount": {"forecasts": {}}}}))
    assert power.answer_reasoning(tmp_path, "sol-MOCK1", "sullivan_post") == "MOCK: losing raises the chance."
    assert power.answer_reasoning(tmp_path, "sol-MOCK1", "recount") is None
    assert power.answer_reasoning(tmp_path, "sol-MOCK1", "contest") is None
    assert power.answer_reasoning(tmp_path, "sol-MOCK9", "sullivan_post") is None
    (run / "response.json").write_text("not json")
    assert power.answer_reasoning(tmp_path, "sol-MOCK1", "sullivan_post") is None


def test_mock_main_quotes_a_reversed_answers_reasoning(tmp_path):
    per_run = write_mock_tables(tmp_path / "in", mock_primaries(OBSERVED_COUNTS, {"opus": 0.3, "sol": 0.6}, seed=29))
    with open(per_run, newline="") as f:
        rows = list(csv.DictReader(f))
    target = next(r for r in rows if r["question"] == "sullivan_post" and float(r["abs_jump"]) >= 0.5)
    for r in rows:  # MOCK: every answer jumps the usual way but the target, whose absolute jump stays the same
        r["jump"] = str(-float(r["abs_jump"]) if r is target else float(r["abs_jump"]))
    with open(per_run, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["run", "family", "arm", "question", "jump", "abs_jump"])
        w.writeheader()
        w.writerows(rows)
    runs = tmp_path / "runs"
    (runs / target["run"]).mkdir(parents=True)
    (runs / target["run"] / "response.json").write_text(json.dumps(
        {"nodes": {"sullivan_post": {"reasoning": "MOCK: losing raises the chance."}}}))
    out = tmp_path / "out"
    assert power.main(["--per-run", str(per_run), "--out", str(out), "--runs", str(runs), "--studies", "20",
                       "--draws", "20", "--sizes", "24", "--waves", "24", "--simulate-max", "24"]) == 0
    text = (out / "summary.md").read_text()
    assert f"  - {target['run']}, sullivan_post: jump -" in text
    assert "a higher chance where Sullivan wins" in text
    assert 'Its reasoning: "MOCK: losing raises the chance."' in text
    assert "with the answers below read the other way round, E1 reads" in text
    assert "With those answers as written, it reads" in text
    assert "sealed session log" not in text  # MOCK runs have no sealed record on file
