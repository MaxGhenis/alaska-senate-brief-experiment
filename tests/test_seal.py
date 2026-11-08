"""scripts/seal.py seals once, only after every run is done, and never twice at the same time."""

import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("seal", Path(__file__).parent.parent / "scripts" / "seal.py")
seal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seal)


@pytest.fixture
def area(tmp_path, monkeypatch):
    """MOCK runs: one collected, one abandoned, one subfleet run whose state the test sets."""
    runs = tmp_path / "runs"
    for label, meta in [("opus-1", {"backend": "subfleet", "job_id": "MOCK-1", "state": "succeeded"}),
                        ("opus-2", {"backend": "subfleet", "job_id": "MOCK-2", "state": "abandoned"}),
                        ("opus-3", {"backend": "subfleet", "job_id": "MOCK-3", "state": "waiting"})]:
        (runs / label).mkdir(parents=True)
        (runs / label / "meta.json").write_text(json.dumps({"label": label, **meta}))
    (runs / "opus-1" / "response.md").write_text("MOCK answer\n")
    monkeypatch.setattr(seal, "RUNS", runs)
    monkeypatch.setattr(seal, "FOLDER", tmp_path / "prereg" / "2026-11-03-collected")
    monkeypatch.setattr(seal, "MARKER", tmp_path / "cache" / "sealing")
    states = {"MOCK-3": "waiting"}
    monkeypatch.setattr(seal, "job_state", lambda job_id: states[job_id])
    return states


def test_only_a_run_that_may_still_answer_is_pending(area):
    assert seal.pending() == {"opus-3": "waiting"}
    area["MOCK-3"] = "running"
    assert seal.pending() == {"opus-3": "running"}
    for done in ("succeeded", "failed", "cancelled", "lost"):
        area["MOCK-3"] = done
        assert seal.pending() == {}


def test_a_fresh_marker_blocks_a_second_sealer_and_a_stale_one_does_not(area):
    assert seal.claim("MOCK first")
    assert not seal.claim("MOCK second")
    old = time.time() - seal.STALE_S - 60
    os.utime(seal.MARKER, (old, old))
    assert seal.claim("MOCK third")
    assert "MOCK third" in seal.MARKER.read_text()


def test_nobody_claims_once_the_sealed_folder_exists(area):
    seal.FOLDER.mkdir(parents=True)
    assert not seal.claim("MOCK late")
    assert not seal.MARKER.exists()


def test_sealing_by_hand_refuses_while_a_run_is_still_going(area, monkeypatch):
    monkeypatch.setattr(seal, "seal", lambda who: pytest.fail("sealed while a run was still going"))
    assert seal.main(["--now"]) == 1
    area["MOCK-3"] = "succeeded"
    monkeypatch.setattr(seal, "seal", lambda who: 0)
    assert seal.main(["--now"]) == 0
    assert not seal.MARKER.exists()


def test_the_fallback_waits_for_its_time_and_gives_up_after_the_deadline(area, monkeypatch):
    area["MOCK-3"] = "succeeded"
    monkeypatch.setattr(seal, "seal", lambda who: pytest.fail("sealed before its time"))
    assert seal.main(["--once", "--act-after", "2999-01-01T00:00:00Z", "--give-up", "2999-01-02T00:00:00Z"]) == 0
    assert seal.main(["--act-after", "2000-01-01T00:00:00Z", "--give-up", "2000-01-02T00:00:00Z"]) == 1
