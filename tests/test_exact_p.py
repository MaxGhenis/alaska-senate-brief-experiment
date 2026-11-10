"""scripts/exact_p.py: exact randomization p-values by counting every re-assignment. Every run and value
here is MOCK data.

Invariants:
- `assignments` lists each way to give the arms their counts exactly once: its rows are distinct, each
  holds every arm's count, and their number is the multinomial coefficient (property test);
- `exact_p` equals a brute-force count over every pair of the two models' assignments, each scored by
  the registered arms.effect (differential test), with one complete family as with two;
- the registered Monte Carlo p-value, arms.randomization_p, lies within its sampling error of the exact
  one (differential test);
- main() writes exact_p.csv and refuses a per-run table that disagrees with its effects.csv.
"""

from __future__ import annotations

import csv
import importlib.util
import itertools
import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from i86 import arms

spec = importlib.util.spec_from_file_location("exact_p", Path(__file__).parent.parent / "scripts" / "exact_p.py")
exact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exact)

NAMES = [name for name, _, _, _ in arms.PRIMARY]


def mock_rows(counts: dict[str, tuple[int, ...]], seed: int, shift: float = 0.0) -> list[tuple[str, str, float]]:
    """MOCK (family, arm, value) rows: normal noise, plus `shift` in the arms that carry the first block."""
    rng = np.random.default_rng(seed)
    return [(fam, arm, float(rng.normal() + shift * arms.MAIN_E[arm]))
            for fam, ns in counts.items() for arm, n in zip(arms.ARMS, ns) for _ in range(n)]


def brute_force(rows, contrast) -> float:
    """The share of all re-assignments (each family's arms permuted, counts kept) whose registered
    estimate lies at least as far from zero as the observed one."""
    observed = arms.effect(rows, contrast).est
    tol = 1e-9 * max(1.0, abs(observed))
    fams = arms.complete_families(arms.cells(rows))
    per_family = []
    for fam in fams:
        values = [v for f, _, v in rows if f == fam]
        labels = [a for f, a, _ in rows if f == fam]
        per_family.append([[(fam, a, v) for a, v in zip(perm, values)] for perm in set(itertools.permutations(labels))])
    hits = total = 0
    for combo in itertools.product(*per_family):
        total += 1
        hits += abs(arms.effect([r for part in combo for r in part], contrast).est) >= abs(observed) - tol
    return hits / total


@settings(max_examples=60, deadline=None)
@given(st.tuples(*[st.integers(0, 3)] * 4).filter(lambda c: 1 <= sum(c) <= 7))
def test_mock_assignments_lists_each_way_once(counts):
    rows = exact.assignments(counts)
    expected = math.factorial(sum(counts)) // math.prod(math.factorial(c) for c in counts)
    assert len(rows) == expected == len({tuple(r) for r in rows.tolist()})
    assert all(tuple(int((r == j).sum()) for j in range(4)) == counts for r in rows)


@pytest.mark.parametrize("counts,seed,contrast", [
    ({"opus": (1, 1, 1, 1), "sol": (2, 1, 1, 1)}, 1, arms.MAIN_E),
    ({"opus": (2, 1, 1, 1), "sol": (1, 2, 1, 1)}, 2, arms.MAIN_P),
    ({"opus": (1, 1, 2, 1), "sol": (1, 1, 1, 1)}, 3, arms.INTERACTION),
])
def test_mock_exact_p_equals_a_brute_force_count(counts, seed, contrast):
    rows = mock_rows(counts, seed, shift=1.0)
    estimate, p, sizes = exact.exact_p(rows, contrast)
    assert estimate == pytest.approx(arms.effect(rows, contrast).est, abs=1e-12)
    assert p == pytest.approx(brute_force(rows, contrast), abs=1e-12)
    assert sizes == {fam: math.factorial(sum(c)) // math.prod(math.factorial(k) for k in c) for fam, c in counts.items()}


def test_mock_exact_p_uses_the_one_complete_family():
    rows = mock_rows({"opus": (2, 1, 1, 1), "sol": (2, 2, 0, 1)}, 4, shift=1.0)  # MOCK: Sol has no run in arm 10
    estimate, p, sizes = exact.exact_p(rows, arms.MAIN_E)
    assert list(sizes) == ["opus"] and estimate == pytest.approx(arms.effect(rows, arms.MAIN_E).est, abs=1e-12)
    assert p == pytest.approx(brute_force(rows, arms.MAIN_E), abs=1e-12)


def test_mock_a_zero_effect_counts_every_assignment():
    rows = [(fam, arm, 1.0) for fam in arms.FAMILIES for arm in arms.ARMS for _ in range(2)]  # MOCK: no variation
    assert exact.exact_p(rows, arms.MAIN_E)[:2] == (0.0, 1.0)


def test_mock_the_registered_monte_carlo_p_agrees_with_the_exact_one():
    rows = mock_rows({"opus": (3, 3, 3, 2), "sol": (3, 2, 3, 2)}, 5, shift=1.2)  # MOCK: the registered cell sizes
    _, p, _ = exact.exact_p(rows, arms.MAIN_E)
    draws = 20_000
    monte_carlo = arms.randomization_p(rows, arms.MAIN_E, draws=draws, seed=3)
    assert abs(monte_carlo - p) < 5 * math.sqrt(p * (1 - p) / draws) + 2 / draws, (monte_carlo, p)


def write_mock_tables(d: Path, tamper: float = 0.0) -> Path:
    """A MOCK per_run.csv in scripts/arms.py's layout and the effects.csv rows for the three primaries."""
    d.mkdir(parents=True, exist_ok=True)
    primaries = {name: mock_rows({"opus": (2, 1, 1, 1), "sol": (1, 1, 2, 1)}, 10 + i, shift=0.5) for i, name in enumerate(NAMES)}
    questions = {name: (nodes, measure) for name, nodes, measure, _ in arms.PRIMARY}
    with open(d / "per_run.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run", "family", "arm", "question", "abs_jump"])
        for i, (fam, arm, _) in enumerate(primaries[NAMES[0]]):
            for name in NAMES:
                for q in questions[name][0]:
                    w.writerow([f"{fam}-MOCK{i:02d}", fam, arm, q, primaries[name][i][2]])
    with open(d / "effects.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["outcome", "estimate", "randomization_p", "registered_primary"])
        for name, _, _, block in arms.PRIMARY:
            w.writerow([name, arms.effect(primaries[name], exact.BLOCK[block]).est + tamper, 0.5, "True"])
    return d / "per_run.csv"


def test_mock_main_writes_the_exact_p_values(tmp_path):
    per_run = write_mock_tables(tmp_path / "in")
    assert exact.main(["--per-run", str(per_run), "--out", str(tmp_path / "out")]) == 0
    rows = list(csv.DictReader(open(tmp_path / "out" / "exact_p.csv")))
    assert [r["primary"] for r in rows] == NAMES
    assert all(0 < float(r["exact_p"]) <= 1 and float(r["holm_exact"]) >= float(r["exact_p"]) for r in rows)
    assert rows[0]["assignments"] == "opus 60; sol 60"


def test_mock_main_refuses_a_per_run_table_that_disagrees_with_effects(tmp_path):
    per_run = write_mock_tables(tmp_path / "in", tamper=0.01)
    with pytest.raises(SystemExit, match="effects.csv says"):
        exact.main(["--per-run", str(per_run), "--out", str(tmp_path / "out")])
