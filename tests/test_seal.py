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
    monkeypatch.setattr(seal, "assigned", lambda: ["opus-1", "opus-2", "opus-3"])  # every label dispatched
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


def test_nobody_seals_again_once_the_folder_is_committed_and_pushed(area, monkeypatch):
    seal.FOLDER.mkdir(parents=True)
    monkeypatch.setattr(seal, "sealed", lambda: True)
    monkeypatch.setattr(seal, "seal", lambda who, last_call=False: pytest.fail("sealed twice"))
    assert seal.main(["--now"]) == 0
    assert seal.main(["--once", "--act-after", "2000-01-01T00:00:00Z"]) == 0
    assert not seal.MARKER.exists()


def test_a_sealed_folder_not_yet_pushed_is_pushed_without_collecting_again(area, monkeypatch):
    """MOCK: the folder exists but its commit never reached GitHub. The next pass skips collection
    (and the still-going check) and only commits and pushes."""
    seal.FOLDER.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(seal, "sealed", lambda: bool(calls))
    monkeypatch.setattr(seal, "pending", lambda now=None: pytest.fail("checked runs after collection"))
    monkeypatch.setattr(seal, "seal", lambda who, last_call=False: calls.append(who) or 0)
    assert seal.main(["--now"]) == 0 and len(calls) == 1


def test_the_fallback_keeps_trying_after_a_failed_pass(area, monkeypatch):
    """MOCK: a pass that raises (subfleet down) and a seal that fails (push refused) each end only that
    pass; the fallback seals on the third."""
    area["MOCK-3"] = "succeeded"
    outcomes = iter([RuntimeError("MOCK subfleet down"), 1, 0])
    passes = []

    def fake_seal(who, last_call=False):
        passes.append(who)
        out = next(outcomes)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(seal, "seal", fake_seal)
    monkeypatch.setattr(seal.time, "sleep", lambda s: None)
    assert seal.main(["--act-after", "2000-01-01T00:00:00Z", "--give-up", "2999-01-01T00:00:00Z"]) == 0
    assert len(passes) == 3 and not seal.MARKER.exists()


def test_a_lost_run_is_listed_for_recovery_whatever_its_arm(area):
    area["MOCK-3"] = "lost"
    assert seal.lost() == (["opus-3"], [])
    meta = json.loads((seal.RUNS / "opus-3" / "meta.json").read_text())
    (seal.RUNS / "opus-3" / "meta.json").write_text(json.dumps({**meta, "recovered": {"at": "MOCK"}}))
    assert seal.lost() == ([], [])


def test_sealing_recovers_lost_runs_first_and_audits_every_codex_thread_before_stamping(area, monkeypatch, tmp_path):
    """MOCK: the order of the steps, with every command faked."""
    area["MOCK-3"] = "lost"
    prereg = tmp_path / "prereg"
    prereg.mkdir(exist_ok=True)
    (prereg / "README.md").write_text("| Folder | Written | Covers |\n|---|---|---|\n| `registration/` | x | y |\n")
    monkeypatch.setattr(seal, "ROOT", tmp_path)
    monkeypatch.setattr(seal, "PREREG", prereg)
    cmds = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(cmd):
        cmds.append([str(c) for c in cmd])
        if cmds[-1][1:] == ["prereg"]:  # a fresh manifest of every answer, and its proof
            answers = sorted(str(p.relative_to(tmp_path)) for p in seal.RUNS.glob("*/response.md"))
            (prereg / "SHA256SUMS").write_text("".join(f"MOCK  {a}\n" for a in ["README.md", *answers]))
            (prereg / "SHA256SUMS.ots").write_text("MOCK\n")
        return Done()

    monkeypatch.setattr(seal, "run", fake_run)
    monkeypatch.setattr(seal, "git", lambda *a: Done())
    monkeypatch.setattr(seal, "sealed", lambda: True)
    assert seal.seal("MOCK") == 0
    steps = [c[1] if c[0].endswith("i86") else Path(c[1]).name if c[0].endswith("python") else c[0] + " " + c[1]
             for c in cmds]
    assert steps[:7] == ["recover", "collect", "check", "audit", "audit_codex_sessions.py", "prereg", "git add"]
    assert cmds[0][-1] == "opus-3"
    assert (seal.FOLDER / "SHA256SUMS.ots").exists()
    readme = (prereg / "README.md").read_text()
    assert readme.count(f"| `{seal.FOLDER.name}/`") == 1
    seal.seal("MOCK again")  # the row is never added twice
    assert (prereg / "README.md").read_text().count(f"| `{seal.FOLDER.name}/`") == 1


def test_sealing_by_hand_refuses_while_a_run_is_still_going(area, monkeypatch):
    monkeypatch.setattr(seal, "seal", lambda who, last_call=False: pytest.fail("sealed while a run was still going"))
    assert seal.main(["--now"]) == 1
    area["MOCK-3"] = "succeeded"
    monkeypatch.setattr(seal, "seal", lambda who, last_call=False: 0)
    assert seal.main(["--now"]) == 0
    assert not seal.MARKER.exists()


def test_the_fallback_waits_for_its_time_and_gives_up_after_the_deadline(area, monkeypatch):
    area["MOCK-3"] = "succeeded"
    monkeypatch.setattr(seal, "seal", lambda who, last_call=False: pytest.fail("sealed before its time"))
    assert seal.main(["--once", "--act-after", "2999-01-01T00:00:00Z", "--give-up", "2999-01-02T00:00:00Z"]) == 0
    assert seal.main(["--act-after", "2000-01-01T00:00:00Z", "--give-up", "2000-01-02T00:00:00Z"]) == 1
