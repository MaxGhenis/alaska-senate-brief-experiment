"""The fifth pre-registration re-review's items (C1, C2, C3), each with a test. MOCK runs, lanes and
Subfleet.

- C1: a run's session audit that stops on its data (a kept copy that differs from the lane's log, a
  log line that is not JSON) writes a failed record and the script goes on to the next run, so a
  missing record means only an interrupted step, which a later pass can mend. An OS error still stops
  the script and writes nothing.
- C2: at the last call, every step before check leaves RESERVE_S before the give-up time for check,
  the audits and prereg: first tries of `i86 recover` included, and Subfleet's state queries and the
  guard pass too. Only collect's first try runs into it, for at most COLLECT_FLOOR_S. Past its cutoff,
  `lost` no longer waits to ask again.
- C3: GAPS.txt names a step that never started for want of time as one that had no time to run, not
  as one that timed out.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from i86 import cli
from test_audit_codex_sessions import acs, search, write_log
from test_review_r16_prereg import _run, seal, sealing  # noqa: F401
from test_review_r16d_prereg import _gaps, _later, _steps, _tries

REAL_RUN = seal.run  # imported before any test fakes it: with no time left, it starts nothing


# --- C1: an audit that stops on its data fails its run, and the next run is still audited ----------

def _sol(tmp_path: Path, label: str) -> tuple[Path, str]:
    """A MOCK collected run a Codex lane served, with its check and transcript-audit records."""
    workdir = tmp_path / "work" / label
    workdir.mkdir(parents=True)
    d = tmp_path / "runs" / label
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"label": label, "workdir": str(workdir), "lane_id": "codex-9",
                                             "backend": "subfleet", "job_id": f"MOCK-{label}", "state": "succeeded"}))
    (d / "response.md").write_text("MOCK answer\n")
    (d / "response.json").write_text("{}\n")
    (d / "audit.json").write_text("{}\n")
    return d, str(workdir)


def _main_thread(lanes: Path, label: str, workdir: str, tail: str = "") -> Path:
    path = write_log(lanes, f"rollout-MOCK-{label}.jsonl",
                     {"id": f"MOCK-{label}", "cwd": workdir, "source": "exec", "timestamp": "2026-11-09T12:10:00Z"},
                     items=[search(f"MOCK {label} query")])
    if tail:
        with path.open("a") as fh:
            fh.write(tail)
    return path


@pytest.fixture
def two_sol(tmp_path, monkeypatch):
    """Two MOCK Codex runs, each with its main thread's log in the lanes: sol-01 is the one a test
    breaks, and sol-02's audit passes."""
    monkeypatch.setattr(acs, "RUNS", tmp_path / "runs")
    lanes = tmp_path / "lanes"
    runs = {label: _sol(tmp_path, label) for label in ("sol-01", "sol-02")}
    logs = {label: _main_thread(lanes, label, workdir) for label, (_, workdir) in runs.items()}
    return SimpleNamespace(lanes=lanes, bad=runs["sol-01"][0], good=runs["sol-02"][0], log=logs["sol-01"],
                           workdir=runs["sol-01"][1])


def _record(d: Path) -> dict | None:
    path = d / "codex-sessions" / "audit.json"
    return json.loads(path.read_text()) if path.exists() else None


def test_mock_c1_a_kept_copy_that_differs_fails_its_run_and_the_next_run_is_still_audited(two_sol):
    kept = two_sol.bad / "codex-sessions" / two_sol.log.name
    kept.parent.mkdir()
    kept.write_text(two_sol.log.read_text() + "\n")  # kept by an earlier pass, before the lane's log changed
    assert acs.main(["--lanes", str(two_sol.lanes)]) == 1
    failed = _record(two_sol.bad)
    assert failed["passed"] is False and failed["threads"] == []
    assert failed["error"].startswith("SystemExit: ") and "differs" in failed["flags"][0]
    assert failed["workdir"] == two_sol.workdir
    assert _record(two_sol.good)["passed"] is True


def test_mock_c1_a_log_line_that_is_not_json_fails_its_run_and_the_next_run_is_still_audited(two_sol):
    with two_sol.log.open("a") as fh:
        fh.write('{"type": "event_msg", "payl')  # cut mid-line, as a lane killed mid-write leaves it
    assert acs.main(["--lanes", str(two_sol.lanes)]) == 1
    failed = _record(two_sol.bad)
    assert failed["passed"] is False and failed["error"].startswith("JSONDecodeError: ")
    assert failed["flags"] == [f"the audit stopped: {failed['error'].split(': ', 1)[1]}"]
    assert _record(two_sol.good)["passed"] is True


@pytest.mark.parametrize("exc, recorded", [
    (SystemExit("MOCK differs"), True), (ValueError("MOCK"), True), (TypeError("MOCK"), True),
    (AttributeError("MOCK"), True), (KeyError("MOCK"), True), (IndexError("MOCK"), True),
    (RecursionError("MOCK"), True), (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "MOCK"), True),
    (OSError("MOCK disk"), False), (FileNotFoundError("MOCK gone"), False), (PermissionError("MOCK"), False),
    (KeyboardInterrupt(), False)])
def test_mock_c1_only_an_error_a_later_pass_would_meet_again_writes_a_failed_record(two_sol, monkeypatch, exc,
                                                                                     recorded):
    real = acs.audit_run

    def audit_run(d, lanes, heads=None):
        if d.name == "sol-01":
            raise exc
        return real(d, lanes, heads)

    monkeypatch.setattr(acs, "audit_run", audit_run)
    if recorded:
        assert acs.main(["--lanes", str(two_sol.lanes)]) == 1
        assert _record(two_sol.bad)["error"].startswith(type(exc).__name__)
        assert _record(two_sol.good)["passed"] is True
    else:
        with pytest.raises(type(exc)):
            acs.main(["--lanes", str(two_sol.lanes)])
        assert _record(two_sol.bad) is None and _record(two_sol.good) is None


def test_mock_c1_the_analysis_reads_a_stopped_audit_as_a_failed_one(two_sol):
    with two_sol.log.open("a") as fh:
        fh.write("MOCK not JSON\n")
    acs.main(["--lanes", str(two_sol.lanes)])
    session = cli._session_audit(two_sol.bad)
    assert isinstance(session, dict) and session["passed"] is False  # accepted_runs: "the audit ... failed"


def test_mock_c1_after_a_stopped_audit_no_answer_lacks_its_record(two_sol, monkeypatch):
    """C1's sequence: before the fix, sol-01's stopped audit left sol-02 without a record too, and
    `unrecorded` stopped every seal pass until the last call."""
    with two_sol.log.open("a") as fh:
        fh.write('{"type": "event_msg", "payl')
    acs.main(["--lanes", str(two_sol.lanes)])
    monkeypatch.setattr(seal, "RUNS", acs.RUNS)
    assert seal.unrecorded() == []


# --- C2: at the last call, nothing before check runs into the reserve but collect's first try -----

GRID = [1, 60, 119, 120, 121, 300, 599, 601, 660, 719, 720, 721, 899, 900, 901, 1200, 1800]


def _lost_run(sealing):
    _run(sealing, "sol-01", state="running", answer=False)
    sealing.states["MOCK-sol-01"] = "lost"


@pytest.mark.parametrize("left_s", GRID)
def test_mock_c2_at_the_last_call_recovers_first_try_leaves_the_reserve(sealing, monkeypatch, left_s):
    _lost_run(sealing)
    limits = _tries(sealing, monkeypatch, "recover", [0])
    seal.seal("MOCK", last_call=True, give_up=_later(left_s))
    expected = min(seal.LIMIT["recover"], left_s, max(left_s - seal.RESERVE_S, 0))
    assert limits[0] == pytest.approx(expected, abs=1)


@pytest.mark.parametrize("left_s", GRID)
def test_mock_c2_at_the_last_call_collects_first_try_gets_at_least_its_floor(sealing, monkeypatch, left_s):
    _run(sealing, "opus-01", answer=False)
    limits = _tries(sealing, monkeypatch, "collect", [0])
    seal.seal("MOCK", last_call=True, give_up=_later(left_s))
    expected = min(seal.LIMIT["collect"], left_s, max(left_s - seal.RESERVE_S, seal.COLLECT_FLOOR_S))
    assert limits[0] == pytest.approx(expected, abs=1) and limits[0] > 0


@pytest.mark.parametrize("left_s", GRID)
@pytest.mark.parametrize("step", ["recover", "collect"])
def test_mock_c2_no_try_runs_into_the_reserve_but_collects_first(sealing, monkeypatch, left_s, step):
    """The invariant, for every try of a step that keeps failing: no try's limit exceeds the time left,
    or the time left minus RESERVE_S, except that collect's first try may take up to COLLECT_FLOOR_S;
    a later try starts only with time to run, and with time to run, all three start."""
    _lost_run(sealing) if step == "recover" else _run(sealing, "opus-01", answer=False)
    start = datetime.now(timezone.utc)
    limits = _tries(sealing, monkeypatch, step, [1] * 10)
    seal.seal("MOCK", last_call=True, give_up=start + timedelta(seconds=left_s))
    for k, limit in enumerate(limits):
        floor = seal.COLLECT_FLOOR_S if step == "collect" and k == 0 else 0
        assert limit <= min(left_s, max(left_s - seal.RESERVE_S, floor)) + 1e-6
    assert all(limit > 0 for limit in limits[1:])
    assert len(limits) == (seal.LAST_CALL_TRIES if left_s > seal.RESERVE_S else 1)


@pytest.mark.parametrize("last_call", [False, True])
def test_mock_c2_at_the_last_call_seals_state_queries_stop_short_of_the_reserve(sealing, monkeypatch, last_call):
    _run(sealing, "sol-01", state="running", answer=False)
    asked = []
    monkeypatch.setattr(seal, "job_state", lambda job_id, give_up=None: asked.append(give_up) or "lost")
    give_up = _later(1800)
    seal.seal("MOCK", last_call=last_call, give_up=give_up)
    assert asked and set(asked) == {give_up - timedelta(seconds=seal.RESERVE_S) if last_call else give_up}


@pytest.mark.parametrize("ahead_s, last_call", [(1000, True), (1700, True), (1900, False), (7200, False)])
def test_mock_c2_at_the_last_call_mains_queries_and_guard_pass_stop_short_of_the_reserve(sealing, monkeypatch,
                                                                                         ahead_s, last_call):
    _run(sealing, "opus-01", state="running", answer=False)
    asked = []
    monkeypatch.setattr(seal, "job_state", lambda job_id, give_up=None: asked.append(give_up) or "running")
    give_up = (datetime.now(timezone.utc) + timedelta(seconds=ahead_s)).replace(microsecond=0)
    assert seal.main(["--once", "--act-after", "2000-01-01T00:00:00Z",
                      "--give-up", give_up.strftime("%Y-%m-%dT%H:%M:%SZ")]) == 0
    ask_by = give_up - timedelta(seconds=seal.RESERVE_S) if last_call else give_up
    assert set(asked) == {ask_by} and len(asked) == (2 if last_call else 1)
    if last_call:
        guard = sealing.commands.timeouts["deadline_guard.py"]
        assert guard == pytest.approx(min(seal.LIMIT["guard"], (ask_by - datetime.now(timezone.utc)).total_seconds()),
                                      abs=2)
    else:
        assert "deadline_guard.py" not in sealing.commands.timeouts
    assert not seal.FOLDER.exists()


def test_mock_c2_past_its_cutoff_lost_asks_once_and_never_waits(sealing, monkeypatch):
    _run(sealing, "sol-01", state="running", answer=False)
    monkeypatch.setattr(seal, "RETRY_S", 5)
    asked, waits = [], []
    monkeypatch.setattr(seal, "job_state", lambda job_id, give_up=None: asked.append(job_id))
    monkeypatch.setattr(seal.time, "sleep", waits.append)
    assert seal.lost(_later(-1)) == ([], ["sol-01"])
    assert asked == ["MOCK-sol-01"] and waits == []
    assert seal.lost(_later(60)) == ([], ["sol-01"])
    assert asked == ["MOCK-sol-01"] * 3 and waits == [5]


# --- C3: a step that never started is named as one that had no time to run ------------------------

def test_mock_c3_at_the_last_call_a_recover_with_no_time_left_is_named_as_such(sealing, monkeypatch):
    _lost_run(sealing)
    _run(sealing, "opus-01")
    fake = seal.run
    monkeypatch.setattr(seal, "run", lambda cmd, timeout=600: REAL_RUN(cmd, timeout) if timeout <= 0
                        else fake(cmd, timeout))
    assert seal.seal("MOCK", last_call=True, give_up=_later(seal.RESERVE_S - 60)) == 0
    assert "recovering sol-01 had no time to run, so its answer is not collected" in _gaps().splitlines()
    assert "recover" not in _steps(sealing) and "collect" in _steps(sealing)


@pytest.mark.parametrize("step, never_started, gap", [
    ("check", True, "i86 check had no time to run, so it left no record"),
    ("audit", True, "i86 audit had no time to run, so it left no record"),
    ("audit_codex_sessions.py", True, "the session audit had no time to run, so it left no record"),
    ("collect", True, "collect had no time to run, so it copied no answers"),
    ("check", False, "i86 check timed out, so its record is incomplete"),
    ("audit_codex_sessions.py", False, "the session audit timed out, so its record is incomplete")])
def test_mock_c3_gaps_tell_a_step_that_never_started_from_one_that_timed_out(sealing, monkeypatch, step,
                                                                              never_started, gap):
    _run(sealing, "opus-01", answer=step != "collect")
    fake = seal.run

    def run(cmd, timeout=600):
        argv = [str(c) for c in cmd]
        name = argv[1] if argv[0].endswith("/i86") else Path(argv[1]).name if argv[0].endswith("/python") else None
        if name != step:
            return fake(cmd, timeout)
        if never_started:
            return REAL_RUN(cmd, 0)
        sealing.commands.calls.append(argv)
        return SimpleNamespace(returncode=124, stdout="", stderr=f"timed out after {timeout:.0f} s; killed")

    monkeypatch.setattr(seal, "run", run)
    assert seal.seal("MOCK", last_call=True, give_up=_later(1800)) == 0
    assert gap in _gaps().splitlines()
    assert ("timed out" in _gaps()) == (not never_started)
