"""The scoring rule every brief states (src/i86/score.py). Every forecast and outcome here is invented
MOCK data."""

from __future__ import annotations

import math

import pytest
import yaml
from hypothesis import given, settings, strategies as st

from i86 import score
from i86.response import parse

from test_arms import mock_response

unit = st.floats(0, 1, allow_nan=False)
real = st.floats(-1e3, 1e3, allow_nan=False, allow_infinity=False)


@given(p=unit, happened=st.booleans())
def test_mock_brier_is_the_squared_miss(p, happened):
    assert score.brier(p, happened) == pytest.approx((p - happened) ** 2)
    assert score.brier(float(happened), happened) == 0


@given(x=real, q=st.floats(0.01, 0.99), y=real)
def test_mock_pinball_is_nonnegative_and_zero_only_on_the_outcome(x, q, y):
    loss = score.pinball(x, q, y)
    assert loss >= 0
    assert (loss == 0) == (x == y) or math.isclose(loss, 0, abs_tol=1e-300)


@given(x=real, y=real)
def test_mock_the_median_alone_scores_half_the_absolute_error(x, y):
    assert score.quantile_score([x], [0.5], y) == pytest.approx(abs(x - y) / 2)


@given(cdf=st.lists(unit, min_size=7, max_size=7).map(sorted), y=st.floats(0.3, 0.7))
def test_mock_rps_matches_its_category_form(cdf, y):
    """Differential: the sum over range ends of squared CDF misses equals the sum over categories of
    squared cumulative-probability misses, computed from the probabilities of the eight ranges."""
    edges = [0.485, 0.49, 0.495, 0.5, 0.505, 0.51, 0.515]
    probs = [cdf[0], *(b - a for a, b in zip(cdf, cdf[1:])), 1 - cdf[-1]]
    k = sum(y > e for e in edges)  # the range y falls in
    outcome = [1.0 if i == k else 0.0 for i in range(8)]
    by_category = sum((sum(probs[:i + 1]) - sum(outcome[:i + 1])) ** 2 for i in range(7))
    assert score.rps(cdf, edges, y) == pytest.approx(by_category, abs=1e-12)


def test_mock_a_step_cdf_on_the_outcome_scores_zero_and_its_mirror_scores_most():
    edges = [0.485, 0.49, 0.495, 0.5, 0.505, 0.51, 0.515]
    y = 0.5083
    right = [float(y <= e) for e in edges]
    assert score.rps(right, edges, y) == 0
    assert score.rps([1 - f for f in right], edges, y) == 7


@pytest.mark.parametrize("share, label", [
    (0.40, "dem_to_48.5"), (0.485, "dem_to_48.5"), (0.4850001, "dem_48.5_49.0"), (0.4999, "dem_49.5_50.0"),
    (0.5001, "dem_50.0_50.5"), (0.505, "dem_50.0_50.5"), (0.515, "dem_51.0_51.5"), (0.5150001, "dem_over_51.5"),
    (0.70, "dem_over_51.5"),
])
def test_mock_each_share_falls_in_its_range(tx_graph, share, label):
    assert tx_graph.label(score.realized_cell(tx_graph, share)) == label


@given(share=st.floats(0.0, 1.0).filter(lambda s: s != 0.5))
def test_mock_every_share_but_a_tie_falls_in_exactly_one_range(tx_graph, share):
    assert score.realized_cell(tx_graph, share) in tx_graph.cells


def test_a_tie_is_void(tx_graph):
    assert score.realized_cell(tx_graph, 0.5) is None


def write_outcomes(tmp_path, entries: dict):
    path = tmp_path / "outcomes.yaml"
    path.write_text(yaml.safe_dump(entries))
    return path


SOURCE = {"as_of": "2026-11-25", "source": "MOCK"}


def mock_outcomes(tmp_path, g, peltola=105_000, sullivan=100_000):
    return score.load_outcomes(g, write_outcomes(tmp_path, {
        "dem_share": {"peltola": peltola, "sullivan": sullivan, **SOURCE},
        "recount": {"value": True, **SOURCE},
        "bm2_yes_share": {"value": 48.0, **SOURCE},
        "gov_dem_share": {"void": "MOCK: the ticket was not in the last round", **SOURCE},
    }))


def test_mock_only_the_realized_ranges_forecasts_count(tmp_path, tx_graph):
    """Changing every forecast outside the realized range leaves every score unchanged."""
    g = tx_graph
    outcomes = mock_outcomes(tmp_path, g)  # 51.22% for Peltola: dem_51.0_51.5
    base = mock_response(g, lambda node, x: 0.4)
    fc = parse(g, base)
    rows = score.score_run(g, fc, outcomes)
    assert {r["range"] for r in rows} == {"dem_51.0_51.5"}
    assert {r["question"] for r in rows} == {"dem_share", "recount", "bm2_yes_share"}
    for qid in ("recount", "bm2_yes_share"):
        for key, v in base["nodes"][qid]["forecasts"].items():
            if key != "dem_51.0_51.5":
                for k in v:
                    v[k] = 0.9 if qid == "recount" else 69.0
    assert score.score_run(g, parse(g, base), outcomes) == rows
    by_q = {r["question"]: r["score"] for r in rows}
    assert by_q["recount"] == pytest.approx((0.4 - 1) ** 2)


def test_mock_a_void_or_missing_senate_result_scores_nothing(tmp_path, tx_graph):
    fc = parse(tx_graph, mock_response(tx_graph, lambda node, x: 0.4))
    assert score.score_run(tx_graph, fc, {}) == []
    tie = mock_outcomes(tmp_path, tx_graph, peltola=100_000, sullivan=100_000)
    assert score.score_run(tx_graph, fc, tie) == []
    void = score.load_outcomes(tx_graph, write_outcomes(tmp_path, {
        "dem_share": {"void": "MOCK: a third candidate reached the last round", **SOURCE}}))
    assert score.score_run(tx_graph, fc, void) == []


@pytest.mark.parametrize("entries, message", [
    ({"nope": {"value": 1, **SOURCE}}, "not a question"),
    ({"recount": {"value": 1, **SOURCE}}, "true or false"),
    ({"bm2_yes_share": {"value": True, **SOURCE}}, "is a number"),
    ({"bm2_yes_share": {"value": 48.0}}, "needs as_of and source"),
    ({"dem_share": {"value": 50.2, **SOURCE}}, "peltola and sullivan"),
    ({"dem_share": {"peltola": -1, "sullivan": 3, **SOURCE}}, "vote counts"),
    ({"recount": {"void": "MOCK", "value": True, **SOURCE}}, "only void"),
])
def test_mock_a_bad_outcome_is_refused(tmp_path, tx_graph, entries, message):
    with pytest.raises(score.OutcomeError, match=message):
        score.load_outcomes(tx_graph, write_outcomes(tmp_path, entries))


@settings(max_examples=30, deadline=None)
@given(p=unit, happened=st.booleans())
def test_mock_brier_rewards_honesty(p, happened):
    """Properness: a forecaster who believes p does best in expectation by reporting p."""
    expected = lambda r: p * score.brier(r, True) + (1 - p) * score.brier(r, False)  # noqa: E731
    assert all(expected(p) <= expected(r) + 1e-12 for r in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0))
