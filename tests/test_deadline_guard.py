"""scripts/deadline_guard.py: what it stops, and when. MOCK runs only; no Subfleet call is made."""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("guard", Path(__file__).parent.parent / "scripts" / "deadline_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.fixture
def area(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    for label, meta in [("opus-01", {"job_id": "MOCK-1"}), ("opus-02", {"job_id": "MOCK-2"}),
                        ("sol-01", {}), ("sol-02", {"state": "failed"})]:
        (runs / label).mkdir(parents=True)
        (runs / label / "meta.json").write_text(json.dumps({"label": label, "backend": "subfleet", **meta}))
    monkeypatch.setattr(guard, "RUNS", runs)
    states = {"MOCK-1": "running", "MOCK-2": "killed"}
    monkeypatch.setattr(guard, "job_state", lambda job_id: states[job_id])
    stopped, abandoned = [], []
    monkeypatch.setattr(guard, "stop", lambda label, job_id, reason: stopped.append(label))
    monkeypatch.setattr(guard, "abandon", lambda label: abandoned.append(label))
    return stopped, abandoned


def test_every_finished_state_counts_and_a_job_less_run_is_abandoned_at_the_kill_time(area):
    stopped, abandoned = area
    assert guard.main(["--once", "--start-by", "2999-01-01T00:00:00Z", "--kill-at", "2999-01-01T03:00:00Z"]) == 0
    assert stopped == [] and abandoned == []  # before the deadline nothing is stopped
    assert guard.main(["--once", "--start-by", "2000-01-01T00:00:00Z", "--kill-at", "2000-01-01T03:00:00Z"]) == 0
    assert stopped == ["opus-01"]  # running at the kill time; opus-02 was already killed
    assert abandoned == ["sol-01"]  # no job and not finished; sol-02's failed dispatch is finished


def test_the_guard_exits_once_every_run_is_finished(area, monkeypatch):
    monkeypatch.setattr(guard.time, "sleep", lambda s: pytest.fail("kept waiting"))
    meta = json.loads((guard.RUNS / "sol-01" / "meta.json").read_text())
    (guard.RUNS / "sol-01" / "meta.json").write_text(json.dumps({**meta, "state": "abandoned"}))
    (guard.RUNS / "opus-01" / "response.md").write_text("MOCK answer\n")
    assert guard.main(["--start-by", "2999-01-01T00:00:00Z", "--kill-at", "2999-01-01T03:00:00Z"]) == 0
