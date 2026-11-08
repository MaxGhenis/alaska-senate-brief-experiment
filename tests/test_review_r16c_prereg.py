"""The third pre-registration re-review's items (R1-R5), each with a test. MOCK runs, lanes and Subfleet.

- R1: a Subfleet reply that hangs, fails or is not JSON costs collect and recover one run, not every
  run; collect leaves abandoned and recovered runs alone; at the last call GAPS.txt names every
  assigned run left without an answer.
- R2: a run recorded as finished is never asked about again, so one failed reply at the last call
  cannot abandon a run whose answer collect may still copy; a run Subfleet fails to answer for is
  asked twice.
- R3: git keeps every file byte for byte, so a checkout's answers match the sealed manifest.
- R4: no command starts with no time left before the give-up time, a state query gets no longer
  than that, and the pass holding the marker keeps it fresh.
- R5: the session audit reads the lanes' logs once per invocation, and keeps each copy and its
  report whole or not at all, outside the folder `i86 prereg` hashes.
"""

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from i86 import cli, runners
from test_review_r16_prereg import _run, area, seal, sealing  # noqa: F401
from test_audit_codex_sessions import acs, search, write_log

ROOT = Path(__file__).resolve().parent.parent
NOT_LOST = sorted(runners.FINISHED - {"lost"})


# --- R2: a run recorded as finished is never asked about, so never abandoned for want of a state ----

@pytest.mark.parametrize("state", NOT_LOST)
def test_a_run_recorded_as_finished_is_not_asked_whether_it_was_lost(sealing, monkeypatch, state):
    _run(sealing, "opus-01", state=state, answer=False)
    monkeypatch.setattr(seal, "job_state", lambda job_id, give_up=None: pytest.fail(f"asked about a {state} run"))
    assert seal.lost() == ([], [])


def test_mock_at_the_last_call_a_succeeded_run_survives_a_failed_reply(sealing):
    """R2's sequence: opus-01 succeeded but is not yet copied, and Subfleet fails every reply."""
    _run(sealing, "opus-01", state="succeeded", answer=False)
    _run(sealing, "sol-01")
    assert seal.seal("MOCK", last_call=True, give_up=datetime.now(timezone.utc) + timedelta(minutes=20)) == 0
    assert not any(c[1:2] == ["abandon"] for c in sealing.commands.calls)
    assert [c[1:] for c in sealing.commands.calls if c[0].endswith("/i86")][0] == ["collect"]
    assert "opus-01 (succeeded)" in (seal.FOLDER / "GAPS.txt").read_text()  # still unanswered: named


@pytest.mark.parametrize("second, found", [("lost", (["opus-01"], [])), (None, ([], ["opus-01"])),
                                            ("running", ([], []))])
def test_a_run_subfleet_fails_to_answer_for_is_asked_twice(sealing, monkeypatch, second, found):
    _run(sealing, "opus-01", state="running", answer=False)
    replies = iter([None, second])
    asked = []
    monkeypatch.setattr(seal, "job_state", lambda job_id, give_up=None: asked.append(job_id) or next(replies))
    assert seal.lost() == found and asked == ["MOCK-opus-01"] * 2


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(st.sampled_from(sorted(runners.FINISHED | {"queued", "running", "waiting"})), min_size=1,
                max_size=6), st.data())
def test_only_a_run_not_recorded_as_finished_or_recorded_lost_is_asked(sealing, monkeypatch, states, data):
    """Property: lost() asks Subfleet about exactly the uncollected, unrecovered runs whose recorded
    state is not a finished one other than lost, and abandons (reports unknown) only among those."""
    for d in sealing.runs.iterdir():
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
    asked, expected = [], set()
    for i, state in enumerate(states):
        label = f"run-{i}"
        collected = data.draw(st.booleans(), label=f"{label} collected")
        _run(sealing, label, state=state, answer=collected)
        if not collected and state not in NOT_LOST:
            expected.add(f"MOCK-{label}")
    monkeypatch.setattr(seal, "job_state", lambda job_id, give_up=None: asked.append(job_id))
    lost_runs, unknown = seal.lost()
    assert set(asked) == expected and lost_runs == []
    assert {f"MOCK-{label}" for label in unknown} == expected


# --- R1: a hung, failed or garbled Subfleet reply costs one run, not the collection -----------------

def _subfleet_meta(area, label, **fields):
    run_dir = runners.prepare(label, area.prompt, "MOCK-sha", "subfleet")
    return run_dir, runners.record(run_dir, job_id=f"MOCK-{label}", **fields)


@pytest.mark.parametrize("fields", [{"state": "abandoned"}, {"state": "succeeded", "recovered": {"at": "MOCK"}}],
                         ids=["abandoned", "recovered"])
def test_collect_leaves_abandoned_and_recovered_runs_alone(area, monkeypatch, fields):
    run_dir, meta = _subfleet_meta(area, "opus-01", **fields)
    monkeypatch.setattr(runners.subprocess, "run", lambda cmd, **kw: pytest.fail(f"asked Subfleet: {cmd}"))
    assert runners.refresh_subfleet(run_dir) == meta


@pytest.mark.parametrize("reply", ["timeout", "rc 1", "not json", "[1, 2]"])
def test_a_reply_that_fails_or_is_not_an_object_is_a_collect_error(area, monkeypatch, reply):
    run_dir, _ = _subfleet_meta(area, "opus-01", state="running")

    def fake(cmd, **kw):
        assert kw.get("timeout") == runners.SUBFLEET_TIMEOUT
        if reply == "timeout":
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        return SimpleNamespace(returncode=1 if reply == "rc 1" else 0, stdout=reply, stderr="MOCK failure")

    monkeypatch.setattr(runners.subprocess, "run", fake)
    runners._stalls.clear()
    meta = runners.refresh_subfleet(run_dir)
    assert meta["state"] == "running" and meta["collect_error"]


def test_after_three_timeouts_the_rest_fail_at_once_and_each_command_starts_afresh(area, monkeypatch):
    calls = []

    def hang(cmd, **kw):
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(runners.subprocess, "run", hang)
    runners._stalls.clear()
    results = [runners.subfleet("runs", "show", f"MOCK-{i}", "--json") for i in range(5)]
    assert [r.returncode for r in results] == [124] * 5 and len(calls) == runners.SUBFLEET_STALLS
    assert "not run" in results[-1].stderr
    for label in ("opus-01", "opus-02"):
        _subfleet_meta(area, label, state="running")
    calls.clear()
    assert cli.cmd_collect(None) == 0  # both still going: nothing copied, nothing failed
    assert len(calls) == 2  # collect cleared the stalls before its own calls


def test_mock_one_hung_run_at_the_last_call_leaves_the_others_collected(area, monkeypatch):
    """R1's sequence, mended: opus-02's every reply hangs; opus-01's and opus-03's answers arrive."""
    for label in ("opus-01", "opus-02", "opus-03"):
        _subfleet_meta(area, label, state="succeeded")

    def fake(cmd, **kw):
        job = cmd[3]
        if job == "MOCK-opus-02":
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        if cmd[-1] == "--json":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"job": {"state": "succeeded"}, "attempts": []}),
                                   stderr="")
        return SimpleNamespace(returncode=0, stdout=f"MOCK answer of {job}\n", stderr="")

    monkeypatch.setattr(runners.subprocess, "run", fake)
    assert cli.cmd_collect(None) == 1  # opus-02 succeeded but its answer did not arrive
    have = {d.name for d in runners.run_dirs() if (d / "response.md").exists()}
    assert have == {"opus-01", "opus-03"}
    assert "timed out" in runners.read_meta(runners.RUNS / "opus-02")["collect_error"]


def test_recover_gives_up_on_a_hung_reply_with_an_error_a_later_try_may_mend(area, monkeypatch):
    _subfleet_meta(area, "sol-01", state="lost")

    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(runners.subprocess, "run", hang)
    with pytest.raises(RuntimeError, match="rc 124") as info:
        cli.cmd_recover(SimpleNamespace(label="sol-01"))
    assert not isinstance(info.value, runners.Unrecoverable)  # not final: `i86 recover` exits 1; the seal tries again
    assert runners.read_meta(runners.RUNS / "sol-01")["state"] == "lost"


def test_at_the_last_call_gaps_name_every_assigned_run_left_without_an_answer(sealing, monkeypatch):
    monkeypatch.setattr(seal, "assigned", lambda: ["opus-01", "sol-01", "opus-02", "sol-02"])
    _run(sealing, "opus-01")
    d = _run(sealing, "sol-01", state="abandoned", answer=False)
    (d / "meta.json").write_text(json.dumps({**json.loads((d / "meta.json").read_text()),
                                             "abandon_reason": "MOCK killed at the deadline"}))
    _run(sealing, "opus-02", state="failed", answer=False)
    assert seal.seal("MOCK", last_call=True, give_up=datetime.now(timezone.utc) + timedelta(minutes=20)) == 0
    gaps = (seal.FOLDER / "GAPS.txt").read_text()
    assert ("no answer in this record for sol-01 (abandoned: MOCK killed at the deadline); opus-02 (failed); "
            "sol-02 (not dispatched)") in gaps
    assert "opus-01" not in gaps and gaps in sealing.commands.message()


def test_before_the_last_call_no_gaps_file_is_written(sealing):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01", state="failed", answer=False)
    assert seal.seal("MOCK") == 0 and not (seal.FOLDER / "GAPS.txt").exists()


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(st.sampled_from(["answered", "abandoned", "failed", "succeeded", "absent"]), min_size=1, max_size=6))
def test_unanswered_names_exactly_the_assigned_runs_without_an_answer(sealing, monkeypatch, kinds):
    """Property: unanswered() lists each assigned label with no response.md, once, in assignment order."""
    for d in list(sealing.runs.iterdir()):
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
    labels = [f"run-{i}" for i in range(len(kinds))]
    monkeypatch.setattr(seal, "assigned", lambda: labels)
    for label, kind in zip(labels, kinds):
        if kind != "absent":
            _run(sealing, label, state="succeeded" if kind == "answered" else kind, answer=kind == "answered")
    named = [entry.split(" ", 1)[0] for entry in seal.unanswered()]
    assert named == [label for label, kind in zip(labels, kinds) if kind != "answered"]


# --- R3: byte for byte ---------------------------------------------------------------------------

def test_git_converts_no_line_endings():
    assert (ROOT / ".gitattributes").read_text().split() == ["*", "-text"]
    proc = subprocess.run(["git", "check-attr", "text", "--", "runs/opus-01/response.md", "README.md"], cwd=ROOT,
                          capture_output=True, text=True)
    assert proc.returncode == 0 and proc.stdout.count(": text: unset") == 2, proc.stdout


# --- R4: nothing at or after the give-up time; the marker stays fresh ------------------------------

@pytest.mark.parametrize("timeout", [0, -5])
def test_with_no_time_left_a_command_does_not_start(tmp_path, monkeypatch, timeout):
    monkeypatch.setattr(seal, "ROOT", tmp_path)
    proc = seal.run(["/bin/sh", "-c", f"touch {tmp_path / 'ran'}"], timeout=timeout)
    assert proc.returncode == 124 and not (tmp_path / "ran").exists()


def test_a_state_query_past_the_give_up_time_asks_nothing(monkeypatch):
    monkeypatch.setattr(seal.subprocess, "run", lambda *a, **kw: pytest.fail("asked Subfleet past the give-up time"))
    assert seal.job_state("MOCK", datetime.now(timezone.utc) - timedelta(seconds=1)) is None


def test_a_state_query_gets_no_longer_than_the_time_left(monkeypatch):
    seen = []
    monkeypatch.setattr(seal.subprocess, "run", lambda cmd, **kw: seen.append(kw["timeout"]) or SimpleNamespace(
        returncode=0, stdout=json.dumps({"job": {"state": "running"}}), stderr=""))
    assert seal.job_state("MOCK", datetime.now(timezone.utc) + timedelta(seconds=10)) == "running"
    assert seal.job_state("MOCK") == "running"
    assert 0 < seen[0] <= 10 and seen[1] == seal.LIMIT["subfleet"]


def test_the_pass_holding_the_marker_keeps_it_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(seal, "MARKER", tmp_path / "marker")
    monkeypatch.setattr(seal, "BEAT_S", 0.05)
    seal.MARKER.write_text("MOCK holder\n")
    old = time.time() - 2 * seal.STALE_S
    os.utime(seal.MARKER, (old, old))
    stop = threading.Event()
    beat = threading.Thread(target=seal.heartbeat, args=(stop,))
    beat.start()
    try:
        for _ in range(100):
            if time.time() - seal.MARKER.stat().st_mtime < seal.STALE_S:
                break
            threading.Event().wait(0.05)
    finally:
        stop.set()
        beat.join(5)
    assert not beat.is_alive() and time.time() - seal.MARKER.stat().st_mtime < seal.STALE_S
    assert not seal.claim("MOCK second")  # a fresh marker is not taken over


def test_a_seal_pass_runs_the_heartbeat_and_stops_it(sealing, monkeypatch):
    started, stopped = [], []

    def beat(stop):
        started.append(1)
        stop.wait(5)
        stopped.append(stop.is_set())

    monkeypatch.setattr(seal, "heartbeat", beat)
    _run(sealing, "opus-01")
    _run(sealing, "sol-01")
    assert seal.main(["--now"]) == 0
    for _ in range(100):
        if stopped:
            break
        threading.Event().wait(0.05)
    assert started == [1] and stopped == [True] and not seal.MARKER.exists()


# --- R5: one scan of the lanes; whole copies and reports, outside the hashed folder ----------------

def _two_codex_runs(tmp_path, monkeypatch):
    lanes = tmp_path / "lanes"
    runs = tmp_path / "runs"
    monkeypatch.setattr(acs, "RUNS", runs)
    out = []
    for label in ("sol-01", "sol-02"):
        workdir = str(tmp_path / "work" / label)
        Path(workdir).mkdir(parents=True)
        run = runs / label
        run.mkdir(parents=True)
        (run / "meta.json").write_text(json.dumps({"label": label, "workdir": workdir, "lane_id": "codex-9"}))
        (run / "response.md").write_text("MOCK answer\n")
        write_log(lanes, f"rollout-MOCK-{label}.jsonl",
                  {"id": f"MOCK-{label}", "cwd": workdir, "source": "exec", "timestamp": "2026-11-03T18:10:00Z"},
                  items=[search(f"MOCK {label} query")])
        out.append(run)
    write_log(lanes, "rollout-MOCK-other.jsonl", {"id": "MOCK-other", "cwd": str(tmp_path / "elsewhere"),
                                                  "source": "exec", "timestamp": "2026-11-03T18:00:00Z"})
    return lanes, out


def test_the_session_audit_reads_each_lane_log_once_for_all_runs(tmp_path, monkeypatch):
    lanes, runs = _two_codex_runs(tmp_path, monkeypatch)
    reads = []
    original = acs.header
    monkeypatch.setattr(acs, "header", lambda path: reads.append(path) or original(path))
    assert acs.main(["--lanes", str(lanes)]) == 0
    lane_reads = [p for p in reads if lanes in p.parents]
    assert len(lane_reads) == len(set(lane_reads)) == 3
    for run in runs:
        report = json.loads((run / "codex-sessions" / "audit.json").read_text())
        assert report["passed"] and [t["thread_id"] for t in report["threads"]] == [f"MOCK-{run.name}"]


def test_a_copy_cut_off_leaves_no_log_and_the_next_pass_copies_it_whole(tmp_path, monkeypatch):
    lanes, runs = _two_codex_runs(tmp_path, monkeypatch)
    run = runs[0]
    real = acs.shutil.copyfile

    def cut(src, dst):
        Path(dst).write_bytes(Path(src).read_bytes()[:10])
        raise KeyboardInterrupt  # the pass is killed midway

    monkeypatch.setattr(acs.shutil, "copyfile", cut)
    with pytest.raises(KeyboardInterrupt):
        acs.audit_run(run, lanes)
    folder = run / "codex-sessions"
    assert not folder.exists() or not any(folder.iterdir())
    assert not [p for p in run.rglob("*") if p.name.endswith(".tmp")]
    monkeypatch.setattr(acs.shutil, "copyfile", real)
    report = acs.audit_run(run, lanes)
    kept = run / "codex-sessions" / f"rollout-MOCK-{run.name}.jsonl"
    assert report["passed"] and kept.read_bytes() == (lanes / "codex-9" / "sessions" / "2026" / "11" / "03"
                                                      / kept.name).read_bytes()


def test_a_report_cut_off_leaves_the_old_report(tmp_path, monkeypatch):
    lanes, runs = _two_codex_runs(tmp_path, monkeypatch)
    run = runs[0]
    acs.audit_run(run, lanes)
    report = run / "codex-sessions" / "audit.json"
    before = report.read_bytes()
    real = os.replace

    def refuse(src, dst):
        if Path(dst).name == "audit.json":
            raise KeyboardInterrupt
        return real(src, dst)

    monkeypatch.setattr(acs.os, "replace", refuse)
    with pytest.raises(KeyboardInterrupt):
        acs.audit_run(run, lanes)
    assert report.read_bytes() == before
    assert not [p for p in run.rglob("*") if p.name.endswith(".tmp")]


def test_a_log_whose_first_line_is_not_a_record_is_skipped(tmp_path):
    for name, text in [("list.jsonl", "[1, 2]\n"), ("bytes.jsonl", b"\xff\xfe\n"), ("empty.jsonl", "")]:
        path = tmp_path / name
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
    heads = acs.read_headers(sorted(tmp_path.glob("*.jsonl")))
    assert {p.name: meta for p, meta in heads} == {"empty.jsonl": {}, "list.jsonl": {}}


# --- check, audit and prereg write whole files ------------------------------------------------------

def test_check_audit_and_prereg_write_through_write_atomic():
    source = (ROOT / "src" / "i86" / "cli.py").read_text()
    for target in ('d / "problems.txt"', 'd / "response.json"', "d / REPAIRS", 'd / "audit.json"', "sums"):
        assert f"runners.write_atomic({target}," in source, target
    assert ".write_text(" not in source.split("def cmd_check", 1)[1].split("def main", 1)[0]
