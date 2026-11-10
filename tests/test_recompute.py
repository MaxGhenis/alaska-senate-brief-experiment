"""scripts/recompute.py: the registered primaries by hand, without the package. The first tests use
MOCK data; the last reads the sealed record.

Invariants:
- `abs_jump` equals the absolute value of the registered arms.fitted_jump for any answers at the six
  closed ranges, probabilities and medians alike (differential property test);
- `main_effect` equals the registered arms.effect in its estimate, standard error and degrees of
  freedom, for each block, on unbalanced MOCK studies (differential property test);
- on the sealed record, the script reproduces every committed analysis's primaries and exits 0, and it
  exits 1 when an effects.csv disagrees.
"""

from __future__ import annotations

import csv
import importlib.util
import shutil
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from i86 import arms

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("recompute", ROOT / "scripts" / "recompute.py")
recompute = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recompute)

SIX = recompute.SULLIVAN + recompute.PELTOLA


@settings(max_examples=200, deadline=None)
@given(st.lists(st.floats(-100, 100), min_size=6, max_size=6), st.sampled_from(["p", "p50"]))
def test_mock_abs_jump_is_the_registered_fitted_jump(ys, key):
    forecasts = {name: {key: y, "p05": -1e9} for name, y in zip(SIX, ys)}  # MOCK answers; p05 must go unread
    ref = arms.fitted_jump((48.75, 49.25, 49.75), ys[:3], (50.25, 50.75, 51.25), ys[3:], 50.0)
    assert recompute.abs_jump(forecasts) == pytest.approx(abs(ref), abs=1e-9)


@settings(max_examples=100, deadline=None)
@given(st.lists(st.integers(2, 4), min_size=8, max_size=8), st.integers(0, 10_000), st.sampled_from([0, 1]))
def test_mock_main_effect_is_the_registered_estimate(counts, seed, digit):
    rng = np.random.default_rng(seed)
    rows = [(fam, arm, float(rng.normal()))  # MOCK values
            for fam, ns in (("opus", counts[:4]), ("sol", counts[4:])) for arm, n in zip(arms.ARMS, ns) for _ in range(n)]
    ref = arms.effect(rows, arms.MAIN_E if digit == 0 else arms.MAIN_P)
    estimate, se, df = recompute.main_effect(rows, digit)
    assert estimate == pytest.approx(ref.est, abs=1e-12)
    assert se == pytest.approx(ref.se, rel=1e-9)
    assert df == pytest.approx(ref.df, rel=1e-9)


def test_the_arms_and_primaries_are_the_registered_ones():
    assert recompute.ARMS == tuple(arms.ARMS)
    assert [(name, nodes) for name, nodes, _ in recompute.PRIMARIES] == [(name, nodes) for name, nodes, _, _ in arms.PRIMARY]
    assert [digit for _, _, digit in recompute.PRIMARIES] == [1 if block == "placebo" else 0 for *_, block in arms.PRIMARY]


@pytest.mark.parametrize("analysis", ["arms", "arms-all-flags", "arms-strict", "arms-deviation-1"])
def test_the_sealed_record_reproduces_each_committed_analysis(analysis):
    """The sealed record, not MOCK data."""
    assert recompute.main(["--analysis", str(ROOT / "outputs" / analysis)]) == 0


def test_a_changed_effects_file_fails_the_check(tmp_path):
    """The sealed record's runs with a tampered copy of the registered effects.csv."""
    shutil.copy(ROOT / "outputs" / "arms" / "per_run.csv", tmp_path / "per_run.csv")
    with open(ROOT / "outputs" / "arms" / "effects.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if r["outcome"] == "E1 effect index" and r["registered_primary"] == "True":
            r["estimate"] = str(float(r["estimate"]) + 1e-6)
    with open(tmp_path / "effects.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    assert recompute.main(["--analysis", str(tmp_path)]) == 1
