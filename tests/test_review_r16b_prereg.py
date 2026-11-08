"""Fixes from the re-review of 2026-11-08 (round 16b, items N1-N7 and the dispatch gap). MOCK answers,
runs, jobs, transcripts and session logs, except one read-only differential on Subfleet's own record.

Invariants these tests state, beside the examples:
- A recovered Sol run's model is the one Subfleet's attestation rule reads from the lane's session log
  for the transcript's thread: the answer Subfleet recorded for each attempt it attested (differential),
  and None when no single log names a model.
- Once the answers are sealed, a run counts only if its response.md is the one the manifest lists.
- The repaired reading takes the last block that parses once trailing commas go, the first reading the
  last that parses as written, and every repair record names both (property test below).
- At the last call a run whose state Subfleet does not give is abandoned and named, so it never holds
  up the seal; no pass starts the stamp at or after the give-up time, or gives it longer than the time
  left; a command past its limit is killed with its whole process group.
- Until KILL_AT, a run folder without meta.json counts as a dispatch starting, in the seal and the guard;
  a guard pass that fails is retried a minute later.
- A file write is whole or not at all, and a run's response.md is written after everything else.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from i86 import cli, runners
from i86.response import extract_json, parse, read_answer
from mock import MOCK_SHA, MOCK_response, MOCK_subfleet_meta
from test_review_r16_prereg import (EARLY, Commands, Waited, _fenced, _guard_run, _run, _soon, area,  # noqa: F401
                                    codex_transcript, guard, guarding, seal, sealing)

THREAD = "01a1b9c5-0000-7000-8000-00000000000a"  # a MOCK thread id, in the shape rollout names carry


def _transcript(thread: str = THREAD, **kw) -> str:
    return codex_transcript(**kw).replace('"MOCK-thread"', json.dumps(thread))


def _rollout(lane: str, thread: str = THREAD, models=("gpt-6.1-sol",), *, meta_id=None, meta_model=None,
             day="2026/11/08", name=None) -> Path:
    """A MOCK session log in runners.LANES: a session_meta record, then one turn_context per model."""
    folder = runners.LANES / lane / "sessions" / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name or f"rollout-{day.replace('/', '-')}T09-00-00-{thread}.jsonl")
    payload = {"id": meta_id or thread, "cwd": "/MOCK/work"}
    if meta_model:
        payload["model"] = meta_model
    events = [{"type": "session_meta", "payload": payload}, {"type": "event_msg", "payload": {"type": "MOCK"}}]
    events += [{"type": "turn_context", "payload": {"model": m, "cwd": "/MOCK/work"}} for m in models]
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


@pytest.fixture
def lanes(tmp_path, monkeypatch):
    monkeypatch.setattr(runners, "LANES", tmp_path / "lanes")
    return runners.LANES


# --- N1: a recovered Sol run takes its model from the lane's session log ----------------------------

def test_the_thread_is_the_one_thread_started_event():
    assert runners.codex_thread(_transcript()) == THREAD
    assert runners.codex_thread("not json\n" + _transcript()) == THREAD
    assert runners.codex_thread(_transcript() + _transcript()) is None  # two threads: which?
    assert runners.codex_thread(codex_transcript().replace('{"type": "thread.started", "thread_id": "MOCK-thread"}\n',
                                                            "")) is None
    assert runners.codex_thread(json.dumps({"type": "thread.started", "thread_id": 7}) + "\n") is None


def test_the_one_session_log_of_the_thread_names_the_model(lanes):
    path = _rollout("codex-2")
    _rollout("codex-2", thread="01a1b9c5-0000-7000-8000-00000000000b", models=["gpt-6-luna"])  # another thread
    assert runners.codex_served("codex-2", THREAD, "gpt-6.1-sol") == ("gpt-6.1-sol", str(path))


@pytest.mark.parametrize("models,meta_model,served", [
    (["gpt-6.1-sol", "gpt-6-luna", "gpt-6.1-sol"], None, "gpt-6-luna"),  # the first that differs
    (["gpt-6.1-sol", "gpt-6.1-sol"], None, "gpt-6.1-sol"),
    ([], "gpt-6-luna", "gpt-6-luna"),  # the session_meta model counts too
    (["gpt-6-astra", "gpt-6-luna"], None, "gpt-6-astra")])
def test_the_served_model_is_the_first_that_differs_from_the_one_requested(lanes, models, meta_model, served):
    _rollout("codex-2", models=models, meta_model=meta_model)
    assert runners.codex_served("codex-2", THREAD, "gpt-6.1-sol")[0] == served


def test_no_single_log_naming_a_model_gives_none(lanes):
    assert runners.codex_served("codex-2", THREAD, "gpt-6.1-sol")[0] is None  # no log at all
    assert runners.codex_served(None, THREAD, "gpt-6.1-sol") == (None, "no lane or thread id")
    assert runners.codex_served("codex-2", None, "gpt-6.1-sol") == (None, "no lane or thread id")
    _rollout("codex-2", models=[])
    assert runners.codex_served("codex-2", THREAD, "gpt-6.1-sol")[0] is None  # a log that names no model
    _rollout("codex-2", day="2026/11/09")
    _rollout("codex-2", day="2026/11/10")
    assert runners.codex_served("codex-2", THREAD, "gpt-6.1-sol")[0] is None  # two logs that do: which?


def test_a_log_is_the_threads_only_if_its_session_meta_says_so(lanes):
    _rollout("codex-2", meta_id="01a1b9c5-0000-7000-8000-0000000000ff")  # named for the thread, opening for another
    assert runners.codex_served("codex-2", THREAD, "gpt-6.1-sol")[0] is None
    other = "01a1b9c5-0000-7000-8000-00000000000c"
    _rollout("codex-3", thread=other, meta_id=THREAD)  # another thread's log, opening for this one: never read
    assert runners.codex_served("codex-3", THREAD, "gpt-6.1-sol")[0] is None
    _rollout("codex-4", name="legacy-session.jsonl")  # a name that is no rollout's is a candidate
    assert runners.codex_served("codex-4", THREAD, "gpt-6.1-sol")[0] == "gpt-6.1-sol"


def test_more_candidates_than_subfleets_limit_leave_the_model_unattested(lanes):
    for i in range(runners.ROLLOUT_LIMIT - 1):
        _rollout("codex-2", name=f"legacy-{i}.jsonl", meta_id=f"MOCK-other-{i}")
    path = _rollout("codex-2")
    assert runners.codex_served("codex-2", THREAD, "gpt-6.1-sol") == ("gpt-6.1-sol", str(path))  # 32: at the limit
    _rollout("codex-2", name="legacy-extra.jsonl", meta_id="MOCK-other-extra")
    served, why = runners.codex_served("codex-2", THREAD, "gpt-6.1-sol")
    assert served is None and f"more than {runners.ROLLOUT_LIMIT}" in why


def test_a_fifo_where_the_log_belongs_is_not_opened(lanes):
    """Opening a FIFO for reading would block until a writer came."""
    folder = runners.LANES / "codex-2" / "sessions" / "2026" / "11" / "08"
    folder.mkdir(parents=True)
    os.mkfifo(folder / f"rollout-2026-11-08T09-00-00-{THREAD}.jsonl")
    started = time.monotonic()
    assert runners.codex_served("codex-2", THREAD, "gpt-6.1-sol")[0] is None
    assert time.monotonic() - started < 5


def test_codex_served_agrees_with_subfleet_on_the_attempts_it_attested(monkeypatch):
    """Differential on Subfleet's record (read-only), where this machine has it: for the 60 Codex attempts
    it attested most recently and every one it found served by another model, whose session logs the lanes
    still keep, codex_served gives the model Subfleet recorded, and the attempt's transcript names the
    thread Subfleet recorded."""
    home = Path.home() / ".subfleet"
    if not (home / "state.sqlite3").is_file() or not (home / "lanes").is_dir():
        pytest.skip("Subfleet's record is not on this machine")
    monkeypatch.setattr(runners, "LANES", home / "lanes")
    con = sqlite3.connect(f"file:{home / 'state.sqlite3'}?mode=ro", uri=True)
    try:
        query = ("SELECT attempt_id, lane_id, native_session_id, model_requested, model_served FROM attempts "
                 "WHERE lane_id LIKE 'codex%' AND attestation = ? AND native_session_id IS NOT NULL "
                 "AND model_served IS NOT NULL ORDER BY started_at DESC LIMIT ?")
        rows = con.execute(query, ("attested", 60)).fetchall() + con.execute(query, ("mismatch", 60)).fetchall()
    finally:
        con.close()
    compared = 0
    for attempt_id, lane, thread, requested, served in rows:
        if not list((home / "lanes" / lane / "sessions").glob(f"*/*/*/rollout-*-{thread}.jsonl")):
            continue  # the lane no longer keeps that log
        got, evidence = runners.codex_served(lane, thread, requested)
        assert got == served, (attempt_id, evidence)
        stdout = home / "jobs" / attempt_id / "stdout"
        if stdout.is_file():
            assert runners.codex_thread(stdout.read_text(errors="replace")) == thread, attempt_id
        compared += 1
    if compared < 10:
        pytest.skip(f"only {compared} attested attempts still have their session logs")


def _lost_unattested(area, monkeypatch, transcript, lane="codex-2", native=None):
    """A MOCK lost Sol job whose one attempt Subfleet quarantined without recording a model."""
    run_dir = runners.prepare("sol-01", area.prompt, "sha", "subfleet", family="sol")
    runners.record(run_dir, job_id="MOCK-sol-01")
    attempt = runners.JOBS / "MOCK-sol-01" / "a1"
    attempt.mkdir(parents=True)
    (attempt / "stdout").write_text(transcript)
    (attempt / "exit.json").write_text(json.dumps({"rc": 0, "signal": None}))
    info = {"job": {"state": "lost", "finished_at": "2026-11-09T00:00:00Z"},
            "attempts": [{"attempt_id": "MOCK-sol-01/a1", "state": "quarantined", "lane_id": lane,
                          "model_requested": "gpt-6.1-sol", "model_served": None, "attestation": "unattested",
                          "native_session_id": native, "quarantine_reason": "MOCK writers remain after exit"}]}
    monkeypatch.setattr(runners.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(info), stderr=""))
    return run_dir


def test_mock_a_recovered_sol_run_takes_its_model_from_the_session_log(area, lanes, monkeypatch):
    path = _rollout("codex-2")
    run_dir = _lost_unattested(area, monkeypatch, _transcript())
    assert cli.cmd_recover(argparse.Namespace(label="sol-01")) == 0
    meta = runners.read_meta(run_dir)
    assert meta["model_served"] == "gpt-6.1-sol" and meta["recovered"]["model_evidence"] == str(path)


def test_mock_subfleets_thread_id_wins_over_the_transcripts(area, lanes, monkeypatch):
    other = "01a1b9c5-0000-7000-8000-00000000000d"
    _rollout("codex-2", thread=other, models=["gpt-6-luna"])
    run_dir = _lost_unattested(area, monkeypatch, _transcript(), native=other)
    assert cli.cmd_recover(argparse.Namespace(label="sol-01")) == 0
    assert runners.read_meta(run_dir)["model_served"] == "gpt-6-luna"


def test_mock_a_recovered_sol_run_with_no_log_is_recovered_without_a_model_and_stays_out(area, lanes, monkeypatch,
                                                                                         capsys):
    run_dir = _lost_unattested(area, monkeypatch, _transcript())
    assert cli.cmd_recover(argparse.Namespace(label="sol-01")) == 0
    meta = runners.read_meta(run_dir)
    assert meta["state"] == "succeeded" and meta["model_served"] is None
    assert "expected exactly one" in meta["recovered"]["model_evidence"]
    # accepted_runs reaches the model check: the brief, the finish time and a parsed answer are in order.
    MOCK_subfleet_meta(run_dir, **{**meta, "prompt_sha256": MOCK_SHA})
    (run_dir / "response.json").write_text(json.dumps({"MOCK": "never parsed"}))
    assert cli.accepted_runs(None, MOCK_SHA) == {}
    assert "a sol run that no record shows gpt-6.1-sol served" in capsys.readouterr().err


# --- N2: once sealed, only the answers the manifest lists count -------------------------------------

def _pooled(tmp_path, monkeypatch, graph, labels):
    monkeypatch.setattr(runners, "RUNS", tmp_path / "runs")
    for i, label in enumerate(labels):
        d = runners.RUNS / label
        MOCK_subfleet_meta(d)
        (d / "response.md").write_text(_fenced(MOCK_response(graph, seed=i)))
        (d / "response.json").write_text(json.dumps(MOCK_response(graph, seed=i)))
        (d / "audit.json").write_text(json.dumps({"events": 3, "passed": True, "violations": [], "suspect": []}))


def _seal_manifest(tmp_path, monkeypatch, labels):
    sums = tmp_path / "prereg" / "collected" / "SHA256SUMS"
    sums.parent.mkdir(parents=True)
    lines = ["MOCK  README.md"]
    for label in labels:
        data = (runners.RUNS / label / "response.md").read_bytes()
        lines.append(f"{hashlib.sha256(data).hexdigest()}  runs/{label}/response.md")
    sums.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(cli, "SEALED", sums)


def test_before_the_seal_every_checked_run_counts(tmp_path, monkeypatch, graph):
    _pooled(tmp_path, monkeypatch, graph, ["opus-01", "opus-02"])
    assert cli.sealed_answers() is None
    assert sorted(cli.accepted_runs(graph, MOCK_SHA)) == ["opus-01", "opus-02"]


def test_after_the_seal_an_answer_the_manifest_lacks_or_lists_otherwise_stays_out(tmp_path, monkeypatch, graph,
                                                                                capsys):
    _pooled(tmp_path, monkeypatch, graph, ["opus-01", "opus-02", "opus-03"])
    _seal_manifest(tmp_path, monkeypatch, ["opus-01", "opus-03"])
    with (runners.RUNS / "opus-03" / "response.md").open("a") as f:
        f.write("MOCK line added after the seal\n")
    assert sorted(cli.accepted_runs(graph, MOCK_SHA)) == ["opus-01"]
    err = capsys.readouterr().err
    assert "excluding opus-02: its answer is not the one the sealed manifest" in err
    assert "excluding opus-03: its answer is not the one the sealed manifest" in err


# --- N3: the repaired reading picks its own block, and says which -----------------------------------

def _variant(graph, kind: str, p: float) -> str:
    """One fenced block: "valid" passes as written; "comma" parses only once a trailing comma goes;
    "stray" parses but carries a key the template lacks, with a null value; "bad" parses and carries a
    probability above 1, which nothing mends; "broken" never parses."""
    node, key = next((n.id, next(iter(graph.entries(n))).key) for n in graph.forecast_nodes() if n.kind == "binary")
    obj = MOCK_response(graph)
    obj["nodes"][node]["forecasts"][key] = {"p": 1.5 if kind == "bad" else p}
    if kind == "stray":
        obj["nodes"]["zz_MOCK_note"] = None
    text = json.dumps(obj, indent=1)
    if kind == "comma":
        text = text.replace('"p": ' + json.dumps(p), '"p": ' + json.dumps(p) + ",", 1)
    if kind == "broken":
        text = text[: len(text) // 2]
    return f"```json\n{text}\n```\n"


def _p(graph, obj) -> float:
    node, key = next((n.id, next(iter(graph.entries(n))).key) for n in graph.forecast_nodes() if n.kind == "binary")
    return obj["nodes"][node]["forecasts"][key]["p"]


def test_mock_a_later_block_that_needs_a_comma_beats_an_earlier_one_with_a_stray_key(graph):
    """The re-review's case C: block 1 alone would pass once its stray key went (p=.10)."""
    got, repairs = read_answer(graph, _variant(graph, "stray", 0.1) + "Revised:\n" + _variant(graph, "comma", 0.9))
    parse(graph, got)
    assert _p(graph, got) == 0.9
    assert repairs and all(r["block"] == 2 and r["as_written_block"] == 1 for r in repairs)


def test_mock_a_later_block_that_needs_a_comma_passes_though_an_earlier_one_cannot(graph):
    """The re-review's case D: block 1's probability of 1.5 is a fault nothing mends."""
    got, repairs = read_answer(graph, _variant(graph, "bad", 0.1) + _variant(graph, "comma", 0.9))
    parse(graph, got)
    assert _p(graph, got) == 0.9 and [r["repair"] for r in repairs] == ["trailing_comma"]
    assert repairs[0]["block"] == 2 and repairs[0]["as_written_block"] == 1


def test_where_says_which_block_or_the_outermost_object(graph):
    where = {}
    extract_json(_variant(graph, "valid", 0.2) + _variant(graph, "broken", 0.3), where=where)
    assert where == {"block": 1}
    where = {}
    extract_json("Here it is: " + json.dumps(MOCK_response(graph)), where=where)
    assert where == {"block": "outermost"}


KINDS = ["valid", "comma", "stray", "bad", "broken"]


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(kinds=st.lists(st.sampled_from(KINDS), min_size=1, max_size=4).filter(lambda ks: set(ks) != {"broken"}))
def test_each_reading_takes_the_last_block_it_can_parse_and_the_repairs_name_both(graph, kinds):
    """For any run of blocks: the first reading takes the last block that parses as written; if that
    passes, nothing is repaired. Otherwise the second takes the last block that parses once trailing
    commas go, and every repair record names it and the first reading's."""
    text = "".join(_variant(graph, k, (i + 1) / 10) for i, k in enumerate(kinds))
    as_written = [i for i, k in enumerate(kinds) if k in ("valid", "stray", "bad")]
    repaired = [i for i, k in enumerate(kinds) if k != "broken"]
    first, second = (as_written[-1] if as_written else None), repaired[-1]
    got, repairs = read_answer(graph, text)
    if first is not None and kinds[first] == "valid":
        assert repairs == [] and _p(graph, got) == (first + 1) / 10
        return
    if kinds[second] == "bad":
        with pytest.raises(Exception):
            parse(graph, got)
        return
    parse(graph, got)
    assert _p(graph, got) == (second + 1) / 10
    assert repairs and all(r["block"] == second + 1 for r in repairs)
    assert all(r["as_written_block"] == (None if first is None else first + 1) for r in repairs)


# --- N4, N5, N7: the last call, the gaps it names, and the give-up time ------------------------------

def test_at_the_last_call_a_run_subfleet_gives_no_state_for_does_not_hold_up_the_seal(sealing, monkeypatch):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01", state="running", answer=False)  # sealing.states gives no state for it
    calls = []
    monkeypatch.setattr(seal, "seal", lambda who, last_call=False, give_up=None: calls.append((last_call, give_up)) or 1)
    assert seal.main(["--once", *EARLY]) == 0 and calls == []  # before the last call it waits
    assert seal.main(["--once", "--act-after", "2000-01-01T00:00:00Z", "--give-up", _soon(10)]) == 0
    assert [c[0] for c in calls] == [True] and calls[0][1] is not None
    assert [str(seal.PYTHON), str(seal.GUARD), "--once"] in sealing.commands.calls


def test_a_pass_never_starts_the_stamp_at_or_after_the_give_up_time(sealing):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01")
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert seal.seal("MOCK", last_call=True, give_up=past) == 1
    assert not any(c[1:] == ["prereg"] for c in sealing.commands.calls) and not seal.FOLDER.exists()


def test_the_stamp_gets_no_longer_than_the_time_left(sealing):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01")
    assert seal.seal("MOCK", give_up=datetime.now(timezone.utc) + timedelta(seconds=100)) == 0
    assert 0 < sealing.commands.timeouts["prereg"] <= 100
    assert sealing.commands.timeouts["collect"] <= 100


def test_a_stamp_that_fails_past_the_give_up_time_seals_nothing(sealing, monkeypatch):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01")
    sealing.commands.rc["prereg"] = 1
    fake = seal.run

    def slow(cmd, timeout=600):
        if [str(c) for c in cmd][1:] == ["prereg"]:
            threading.Event().wait(4)  # the fixture stubs time.sleep
        return fake(cmd, timeout)

    monkeypatch.setattr(seal, "run", slow)
    assert seal.seal("MOCK", last_call=True, give_up=datetime.now(timezone.utc) + timedelta(seconds=3)) == 1
    assert not seal.FOLDER.exists()


def test_a_command_past_its_limit_is_killed_with_its_whole_group(tmp_path, monkeypatch):
    monkeypatch.setattr(seal, "ROOT", tmp_path)
    pid_file = tmp_path / "grandchild.pid"
    started = time.monotonic()
    proc = seal.run(["/bin/sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"], timeout=1)
    assert proc.returncode == 124 and time.monotonic() - started < 20
    grandchild = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"the background sleep (pid {grandchild}) outlived the limit")


@pytest.mark.parametrize("module", [seal, guard], ids=["seal", "guard"])
@pytest.mark.parametrize("reply", ["not json", "[1, 2]", "timeout"])
def test_a_reply_that_is_not_a_state_is_no_state(module, reply, monkeypatch):
    def fake(cmd, **kw):
        if reply == "timeout":
            raise module.subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return SimpleNamespace(returncode=0, stdout=reply, stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake)
    assert module.job_state("MOCK-job") is None


# --- N6 and the dispatch gap: the guard and the seal --------------------------------------------------

FUTURE = ["--start-by", "2999-01-01T00:00:00Z", "--kill-at", "2999-01-01T03:00:00Z"]
PAST = ["--start-by", "2000-01-01T00:00:00Z", "--kill-at", "2000-01-01T03:00:00Z"]


def test_until_the_kill_time_a_folder_without_meta_is_a_dispatch_starting(sealing, monkeypatch):
    monkeypatch.setattr(seal, "KILL_AT", "2026-11-10T12:00:00Z")
    (sealing.runs / "opus-01").mkdir()  # prepare() made the folder; meta.json is not written yet
    before, after = (datetime(2026, 11, d, 12, tzinfo=timezone.utc) for d in (8, 10))
    assert seal.pending(before) == {"opus-01": "dispatching", "sol-01": "not dispatched"}
    assert seal.pending(after) == {}


def test_the_guard_waits_on_a_dispatch_starting_until_the_kill_time(guarding):
    _guard_run(guarding, "sol-01", job_id="MOCK-sol-01", state="succeeded")
    (guarding.runs / "opus-01").mkdir()
    with pytest.raises(Waited):
        guard.main(FUTURE)
    assert guard.main(["--once", *FUTURE]) == 0
    assert guard.main(PAST) == 0  # after the kill time the empty folder is no run


def test_a_guard_pass_that_fails_is_retried(guarding, monkeypatch, capsys):
    _guard_run(guarding, "opus-01", job_id="MOCK-opus-01")
    _guard_run(guarding, "sol-01", job_id="MOCK-sol-01")

    def broken(job_id):
        raise RuntimeError("MOCK meta.json mid-write")

    monkeypatch.setattr(guard, "job_state", broken)
    with pytest.raises(Waited):  # the pass failed, and the guard went to sleep for a minute, then retries
        guard.main(FUTURE)
    assert "this pass failed: RuntimeError('MOCK meta.json mid-write')" in capsys.readouterr().out
    assert guard.main(["--once", *FUTURE]) == 1


def test_the_guard_gives_its_reason_when_it_abandons(guarding):
    _guard_run(guarding, "opus-01")  # no job, not finished
    _guard_run(guarding, "sol-01", job_id="MOCK-sol-01", state="succeeded")
    assert guard.main(PAST) == 0
    assert guarding.abandoned == ["opus-01"] and guarding.why == ["no Subfleet job at 2000-01-01T03:00:00Z"]


def test_abandon_keeps_the_reason_given(area):
    runners.prepare("opus-01", area.prompt, "sha", "subfleet")
    assert cli.main(["abandon", "opus-01", "--why", "MOCK reason"]) == 0
    meta = runners.read_meta(runners.RUNS / "opus-01")
    assert meta["state"] == "abandoned" and meta["abandon_reason"] == "MOCK reason"


# --- N6: writes whole or not at all, the answer last -------------------------------------------------

@given(old=st.binary(max_size=200), new=st.binary(max_size=200))
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_a_write_replaces_the_file_whole(tmp_path, old, new):
    path = tmp_path / "meta.json"
    path.write_bytes(old)
    with path.open("rb") as before:  # a reader that opened the old file keeps reading the old bytes
        runners.write_atomic(path, new)
        assert before.read() == old
    assert path.read_bytes() == new
    assert [p.name for p in tmp_path.iterdir()] == ["meta.json"]


def test_a_write_cut_off_leaves_the_old_file_and_no_temporary_one(tmp_path, monkeypatch):
    path = tmp_path / "meta.json"
    path.write_text("MOCK old\n")

    def cut(fd):
        raise OSError("MOCK disk full")

    monkeypatch.setattr(runners.os, "fsync", cut)
    with pytest.raises(OSError):
        runners.write_atomic(path, "MOCK new\n")
    assert path.read_text() == "MOCK old\n" and [p.name for p in tmp_path.iterdir()] == ["meta.json"]


def test_mock_a_collect_cut_off_before_the_answer_leaves_the_run_uncollected_and_the_next_copies_it(area,
                                                                                                    monkeypatch):
    run_dir = runners.prepare("opus-01", area.prompt, "sha", "subfleet")
    runners.record(run_dir, job_id="MOCK-opus-01", state="succeeded", attempt_id="MOCK-opus-01/a1",
                   transcript=str(area.root / "MOCK-stdout"))
    (area.root / "MOCK-stdout").write_text('{"type": "result", "result": "MOCK"}\n')
    monkeypatch.setattr(runners.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="MOCK answer\n", stderr=""))
    write_meta = runners.write_meta

    def cut(run_dir, meta):
        if "collected_at" in meta:
            raise KeyboardInterrupt  # the pass is cut off as it records the collection
        write_meta(run_dir, meta)

    monkeypatch.setattr(runners, "write_meta", cut)
    with pytest.raises(KeyboardInterrupt):
        runners.seal(run_dir)
    assert not (run_dir / "response.md").exists() and not runners.collected_any()
    monkeypatch.setattr(runners, "write_meta", write_meta)
    assert runners.seal(run_dir)
    meta = runners.read_meta(run_dir)
    assert (run_dir / "response.md").read_text() == "MOCK answer\n" and meta["collected_at"]
    assert meta["transcript_original"] == str(area.root / "MOCK-stdout")
