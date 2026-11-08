"""scripts/arms.py end to end on 24 MOCK runs with planted effects. Every run, forecast and audit here
is invented MOCK data."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

from i86 import arms, cli, prompt, runners

from test_arms import mock_response

spec = importlib.util.spec_from_file_location("arms_script", Path(__file__).parent.parent / "scripts" / "arms.py")
arms_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arms_script)

STEP = 0.1  # MOCK: the estimand block adds this jump at 50% to the effect index's questions


def mock_value(arm: str, label: str):
    """MOCK answers flat on each side of 50%. The effect index's questions jump by STEP in arms with
    the estimand block; Measure 2 jumps by 2 * STEP in arms with the placebo block; nothing else jumps."""
    def value(node, x):
        win = x > 50
        if node.id in arms.EFFECT_INDEX and arm[0] == "1":
            return 0.3 + STEP * win
        if node.id == arms.PLACEBO_COACHED and arm[1] == "1":
            return 0.3 + 2 * STEP * win
        return 0.3 + (0.01 if label.endswith("1") else 0.0)  # MOCK run-to-run noise, flat across ranges
    return value


@pytest.fixture
def mock_runs(tmp_path, monkeypatch, tx_graph):
    monkeypatch.setattr(runners, "RUNS", tmp_path / "runs")
    shas = cli._prompt_sha()
    for row in cli.assignment()["runs"]:
        d = tmp_path / "runs" / row["label"]
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({
            "label": row["label"], "arm": row["arm"], "family": row["family"], "order": row["order"],
            "backend": "manual", "prompt_sha256": shas[row["arm"]], "model_served": cli.SERVED[row["family"]]}))
        (d / "response.json").write_text(json.dumps(mock_response(tx_graph, mock_value(row["arm"], row["label"]))))
        (d / "audit.json").write_text(json.dumps({"passed": True, "violations": [], "suspect": []}))
        if row["family"] == "sol":  # a Codex lane serves it: the audit of its session logs must pass too
            (d / "codex-sessions").mkdir()
            (d / "codex-sessions" / "audit.json").write_text(json.dumps({"passed": True, "violations": [], "threads": [{"audit": {"suspect": []}}]}))
    return tmp_path


def read(path: Path) -> list[dict]:
    with open(path) as f:
        return list(csv.DictReader(f))


def test_mock_the_script_recovers_the_planted_effects(mock_runs):
    out = mock_runs / "out"
    assert arms_script.main(["--draws", "300", "--secondary-draws", "50", "--out", str(out)]) == 0
    effects = read(out / "effects.csv")
    primary = {r["outcome"]: r for r in effects if r["registered_primary"] == "True"}
    assert float(primary["E1 effect index"]["estimate"]) == pytest.approx(STEP)
    assert float(primary["P2 uncoached placebo"]["estimate"]) == pytest.approx(0.0, abs=1e-12)
    width = 70 - 30  # bm2_yes_share's bounds: MOCK answers in [0, 1] map onto them
    assert float(primary["P1 coached placebo"]["estimate"]) == pytest.approx(2 * STEP * width)
    assert float(primary["E1 effect index"]["randomization_p"]) < 0.05
    per_run = read(out / "per_run.csv")
    assert len(per_run) == 24 * len(arms.ranged_nodes(cli.graph_mod.load()))
    means = read(out / "arm_means.csv")
    row = next(r for r in means if r["question"] == "recount" and r["measure"] == "jump" and r["arm"] == "11")
    assert float(row["mean"]) == pytest.approx(STEP) and (row["n_opus"], row["n_sol"]) == ("3", "3")
    assert "E1 effect index" in (out / "summary.md").read_text()


def test_mock_a_flagged_run_stays_out_unless_every_flag_is_accepted(mock_runs):
    d = mock_runs / "runs" / "opus-01"
    (d / "audit.json").write_text(json.dumps({"passed": True, "violations": [], "suspect": [{"call": "MOCK"}]}))
    out = mock_runs / "out"
    arms_script.main(["--draws", "50", "--secondary-draws", "10", "--out", str(out)])
    assert "opus-01" not in {r["run"] for r in read(out / "per_run.csv")}
    arms_script.main(["--draws", "50", "--secondary-draws", "10", "--out", str(out), "--accept-all-flags"])
    assert "opus-01" in {r["run"] for r in read(out / "per_run.csv")}
    (d / "audit.json").write_text(json.dumps({"passed": False, "violations": [{"call": "MOCK"}], "suspect": []}))
    arms_script.main(["--draws", "50", "--secondary-draws", "10", "--out", str(out), "--accept-all-flags"])
    assert "opus-01" not in {r["run"] for r in read(out / "per_run.csv")}


def test_mock_a_run_without_its_arm_is_refused(mock_runs):
    d = mock_runs / "runs" / "sol-02"
    meta = json.loads((d / "meta.json").read_text())
    meta.pop("family")
    (d / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(SystemExit, match="lacks a family and arm"):
        arms_script.main(["--draws", "10", "--secondary-draws", "10", "--out", str(mock_runs / "out")])
