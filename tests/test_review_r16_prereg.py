"""Fixes from the pre-registration review of 2026-11-08 (round 16). MOCK answers, runs, jobs and
transcripts only: no Subfleet, git or OpenTimestamps call is made.

Invariants these tests state, beside the examples:
- An answer that passes the check as written comes back unrepaired, so no repair can change what it
  says; one that fails gets only the registered repairs, which never change, add or remove a value
  `parse` reads (property test below).
- A stray key is dropped exactly when its value holds no object at any depth (property test below).
- The seal never stamps before every assigned label has gone out and ended, before KILL_AT; a step a
  later try may mend stops the pass before the last call; nothing seals from a stale manifest.
- The guard never stops a run on a state Subfleet did not give, and never exits before KILL_AT while
  an assigned label has not gone out.
"""

import argparse
import importlib.util
import json
import os
import plistlib
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from i86 import cli, runners
from i86.audit import HOME, _mask_workdir, audit_transcript
from i86.response import ResponseError, _holds_object, _strip_trailing_commas, drop_strays, parse, read_answer
from mock import MOCK_response, MOCK_subfleet_meta

ROOT = Path(__file__).resolve().parent.parent


def _script(name: str):
    spec = importlib.util.spec_from_file_location(f"r16_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seal = _script("seal")
guard = _script("deadline_guard")


def _fenced(answer) -> str:
    text = answer if isinstance(answer, str) else json.dumps(answer, indent=1)
    return f"```json\n{text}\n```\n"


def _binary(graph) -> tuple[str, str]:
    node = next(n for n in graph.forecast_nodes() if n.kind == "binary")
    return node.id, next(iter(graph.entries(node))).key


def _with_trailing_comma(obj) -> str:
    text = json.dumps(obj, indent=1).replace('"notes": null\n', '"notes": null,\n', 1)
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
    return text


# --- Finding 5: an answer that passes as written is read as written ---------------------------------

def test_mock_an_answer_valid_as_written_beats_a_later_block_that_needs_a_repair(graph):
    """MOCK: a draft that passes as written, then a revision that parses only after a repair. The
    draft comes back, unrepaired."""
    node, key = _binary(graph)
    draft = MOCK_response(graph, seed=1)
    draft["nodes"][node]["forecasts"][key] = {"p": 0.1}
    revised = json.loads(json.dumps(draft))
    revised["nodes"][node]["forecasts"][key] = {"p": 0.9}
    got, repairs = read_answer(graph, _fenced(draft) + "On reflection:\n" + _fenced(_with_trailing_comma(revised)))
    assert got == draft and repairs == []


def test_mock_a_later_snippet_that_needs_a_repair_does_not_displace_a_valid_answer(graph):
    """MOCK: a full answer, then a fenced snippet of one question with a trailing comma, which a
    repair would make the last block that parses."""
    obj = MOCK_response(graph)
    node = next(iter(obj["nodes"]))
    snippet = '{"nodes": {"%s": {"reasoning": "MOCK"},}}' % node
    got, repairs = read_answer(graph, _fenced(obj) + "One change:\n" + _fenced(snippet))
    assert got == obj and repairs == []
    parse(graph, got)


def test_mock_when_no_block_passes_as_written_the_repaired_reading_takes_the_last_that_parses(graph):
    """MOCK: an earlier block that parses but lacks a question, then the full answer with a trailing
    comma. As written, the earlier block fails the check, so the repaired reading takes the full one."""
    obj = MOCK_response(graph)
    partial = json.loads(json.dumps(obj))
    partial["nodes"].pop(next(iter(partial["nodes"])))
    got, repairs = read_answer(graph, _fenced(partial) + _fenced(_with_trailing_comma(obj)))
    assert got == obj and [r["repair"] for r in repairs] == ["trailing_comma"]
    parse(graph, got)


# --- Finding 10: a comma that follows no value stays ------------------------------------------------

@pytest.mark.parametrize("text", ['{"nodes": {,}}', "[,]", '{"a": 1,,}', '{"a":,}', "[1,,]", '{"a": [ , ]}',
                                  '{"a": "x", , }'])
def test_a_comma_that_follows_no_value_stays(text):
    assert _strip_trailing_commas(text) == (text, [])


@pytest.mark.parametrize("text,fixed", [('{"a": "x",}', '{"a": "x"}'), ('{"a": 1.5 ,\n}', '{"a": 1.5 \n}'),
                                        ("[true,]", "[true]"), ("[null, ]", "[null ]"), ('{"a": {},}', '{"a": {}}'),
                                        ('{"a": [1],}', '{"a": [1]}'), ('[-2e3,]', "[-2e3]"),
                                        ('{"a": "x,}",}', '{"a": "x,}"}')])
def test_a_comma_that_follows_a_value_goes(text, fixed):
    assert _strip_trailing_commas(text)[0] == fixed
    json.loads(fixed)


def test_mock_an_answer_with_a_hole_still_fails(graph):
    with pytest.raises(ResponseError):
        read_answer(graph, _fenced('{"nodes": {,}}'))


# --- Finding 7: a list that holds an object stays, and fails the check ------------------------------

@pytest.mark.parametrize("stray", [[{"p": 0.95}], [[{"p": 0.95}]], [1, [2, {"p": 0.5}]]])
def test_mock_a_stray_list_that_holds_an_object_stays_and_fails(graph, stray):
    """MOCK: a forecast the run gave under a key the template lacks, inside a list, as in
    "dem_50.0_50.5_if_recount_ordered": [{"p": 0.95}], and a question under a name the map lacks."""
    node, key = _binary(graph)
    obj = MOCK_response(graph)
    obj["nodes"][node]["forecasts"][key + "_if_recount_ordered"] = stray
    got, repairs = read_answer(graph, _fenced(obj))
    assert repairs == []
    with pytest.raises(ResponseError, match="unexpected forecast keys"):
        parse(graph, got)
    obj = MOCK_response(graph)
    obj["nodes"]["recount_alt"] = [{"forecasts": {}}]
    got, repairs = read_answer(graph, _fenced(obj))
    with pytest.raises(ResponseError, match="unexpected nodes"):
        parse(graph, got)


LEAF = (st.none() | st.booleans() | st.integers(-10**6, 10**6) | st.floats(allow_nan=False, allow_infinity=False)
        | st.text(alphabet=',}]{["\\: ab', max_size=8))
OBJECT_FREE = st.recursive(LEAF, lambda kids: st.lists(kids, max_size=3), max_leaves=6)
ANY_JSON = st.recursive(LEAF, lambda kids: st.lists(kids, max_size=3) | st.dictionaries(st.text(max_size=3), kids,
                                                                                         max_size=3), max_leaves=8)


def _has_dict(value) -> bool:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            return True
        if isinstance(item, list):
            stack.extend(item)
    return False


@given(ANY_JSON)
def test_holds_object_matches_a_plain_walk(value):
    assert _holds_object(value) == _has_dict(value)


@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(strays=st.dictionaries(st.text(alphabet="abxyz_", min_size=1, max_size=5), ANY_JSON, max_size=4))
def test_a_stray_key_goes_exactly_when_its_value_holds_no_object(graph, strays):
    node, _ = _binary(graph)
    expected = {n.id: {e.key for e in graph.entries(n)} for n in graph.forecast_nodes()}
    obj = MOCK_response(graph)
    for k, v in strays.items():
        obj["nodes"][node]["forecasts"]["zz_" + k] = v
        obj["nodes"]["zz_" + k] = v
    dropped = {(f["where"], f["key"]) for f in drop_strays(expected, obj)}
    gone = {k for k, v in strays.items() if not _has_dict(v)}
    assert dropped == {(f"{node}.forecasts", "zz_" + k) for k in gone} | {("nodes", "zz_" + k) for k in gone}
    assert {k[3:] for k in obj["nodes"] if k.startswith("zz_")} == set(strays) - gone


def _dumps(value, chosen: set[int], counter: list[int]) -> str:
    """JSON text for `value`, with a trailing comma in each non-empty object or list whose place in a
    depth-first walk is in `chosen`; `counter` counts the non-empty containers walked."""
    if isinstance(value, (dict, list)) and value:
        place = counter[0]
        counter[0] += 1
        parts = ([json.dumps(k) + ": " + _dumps(v, chosen, counter) for k, v in value.items()]
                 if isinstance(value, dict) else [_dumps(v, chosen, counter) for v in value])
        opening, closing = "{}" if isinstance(value, dict) else "[]"
        return opening + ", ".join(parts) + ("," if place in chosen else "") + closing
    return json.dumps(value)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data())
def test_the_registered_repairs_never_change_a_value_parse_reads(graph, data):
    """Property: trailing commas after any values and stray keys whose values hold no object, anywhere a
    run might write them, come back as the clean answer, with each repair recorded."""
    node, _ = _binary(graph)
    obj = MOCK_response(graph, seed=data.draw(st.integers(0, 50)))
    messy = json.loads(json.dumps(obj))
    strays = data.draw(st.dictionaries(st.text(alphabet="abxyz_", min_size=1, max_size=5), OBJECT_FREE, max_size=3))
    for k, v in strays.items():
        messy["nodes"][node]["forecasts"]["zz_" + k] = v
    counter = [0]
    _dumps(messy, set(), counter)
    chosen = data.draw(st.sets(st.integers(0, counter[0] - 1), max_size=8))
    got, repairs = read_answer(graph, _fenced(_dumps(messy, chosen, [0])))
    assert got == obj
    parse(graph, got)
    assert sum(r["count"] for r in repairs if r["repair"] == "trailing_comma") == len(chosen)
    assert {r["key"] for r in repairs if r["repair"] == "dropped_key"} == {"zz_" + k for k in strays}
    assert bool(repairs) == bool(chosen or strays)


# --- Finding 11: one answer that cannot be read fails alone -----------------------------------------

@pytest.fixture
def area(tmp_path, monkeypatch):
    for name in ("RUNS", "WORK", "STAGE", "JOBS"):
        monkeypatch.setattr(runners, name, tmp_path / name.lower())
    prompt = tmp_path / "prompt.md"
    prompt.write_text("MOCK brief\n")
    return SimpleNamespace(root=tmp_path, prompt=prompt)


def test_mock_check_goes_on_past_an_answer_that_raises_any_error(area, graph, monkeypatch, capsys):
    """MOCK: "sources": 3 makes `parse` raise TypeError. That run fails; the next is still checked."""
    monkeypatch.setattr(cli.graph_mod, "load", lambda: graph)
    bad, good = runners.RUNS / "a-bad", runners.RUNS / "b-good"
    for run in (bad, good):
        MOCK_subfleet_meta(run, label=run.name)
    obj = MOCK_response(graph)
    broken = json.loads(json.dumps(obj))
    broken["nodes"][next(iter(broken["nodes"]))]["sources"] = 3
    (bad / "response.md").write_text(_fenced(broken))
    (good / "response.md").write_text(_fenced(obj))
    assert cli.cmd_check(None) == 1
    assert "the answer cannot be read: TypeError" in (bad / "problems.txt").read_text()
    assert not (bad / "response.json").exists()
    assert json.loads((good / "response.json").read_text()) == obj


# --- Finding 2: collect says so when a succeeded run's answer did not arrive -------------------------

def test_mock_collect_exits_1_and_records_why_when_a_succeeded_runs_answer_does_not_arrive(area, monkeypatch, capsys):
    for label in ("opus-01", "opus-02"):
        runners.record(runners.prepare(label, area.prompt, "sha", "subfleet"), job_id=f"MOCK-{label}")
    down = {"MOCK-opus-02"}

    def fake(cmd, **kw):
        assert cmd[:3] == ["subfleet", "runs", "show"], cmd
        job = cmd[3]
        if "--json" in cmd:
            info = {"job": {"state": "succeeded", "finished_at": "2026-11-09T00:00:00Z"},
                    "attempts": [{"attempt_id": f"{job}/a1", "state": "succeeded", "lane_id": "MOCK-lane",
                                  "model_served": "claude-opus-5-5"}]}
            return SimpleNamespace(returncode=0, stdout=json.dumps(info), stderr="")
        if job in down:
            return SimpleNamespace(returncode=1, stdout="", stderr="MOCK daemon not answering")
        return SimpleNamespace(returncode=0, stdout=f"MOCK answer of {job}\n", stderr="")

    monkeypatch.setattr(runners.subprocess, "run", fake)
    assert cli.cmd_collect(None) == 1
    assert "run `i86 collect` again" in capsys.readouterr().out
    assert (runners.RUNS / "opus-01" / "response.md").exists()
    assert not (runners.RUNS / "opus-02" / "response.md").exists()
    assert "MOCK daemon not answering" in runners.read_meta(runners.RUNS / "opus-02")["collect_error"]
    down.clear()
    assert cli.cmd_collect(None) == 0
    assert (runners.RUNS / "opus-02" / "response.md").read_text() == "MOCK answer of MOCK-opus-02\n"


# --- Finding 9: a lost Sol run comes back from its Codex transcript ---------------------------------

def codex_transcript(final="MOCK Sol final", turns=1, failed=False) -> str:
    """A MOCK `codex exec --json` transcript, with the reconnect `error` events a clean run can carry."""
    events = [{"type": "thread.started", "thread_id": "MOCK-thread"}]
    for _ in range(turns):
        events += [{"type": "turn.started"}, {"type": "error", "message": "MOCK reconnecting 1/5"},
                   {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "MOCK draft"}},
                   {"type": "item.completed", "item": {"id": "item_1", "type": "web_search", "query": "MOCK"}},
                   {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": final}},
                   {"type": "turn.failed" if failed else "turn.completed", "usage": {"output_tokens": 1}}]
    return "".join(json.dumps(e) + "\n" for e in events)


CLAUDE_STREAM = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "MOCK"}) + "\n"


def test_codex_final_takes_the_last_agent_message_of_one_completed_turn():
    assert runners.codex_final(codex_transcript()) == ("MOCK Sol final", None)
    assert runners.codex_final("not json\n[1, 2]\n" + codex_transcript()) == ("MOCK Sol final", None)
    for text in (codex_transcript(turns=2), codex_transcript(failed=True), codex_transcript(final=" \n"),
                 CLAUDE_STREAM, ""):
        assert runners.codex_final(text)[1] is not None


@pytest.mark.parametrize("repo", ["texas-governor-data-center-forecasts", "dc-i86-llm-forecast"])
def test_recovery_reads_the_answer_subfleet_handed_over_in_every_sister_study_run(repo):
    """Differential on real runs, where the Texas and DC studies are checked out beside this one. For
    each of their runs, the transcript's final message, read the way `i86 recover` reads it (Codex
    events for Sol, the stream's result event for Opus), is the answer in `response.md`.
    `subfleet runs show --out` prints that message and one newline; a recovered run's answer is the
    message alone (DC opus-3 and opus-4, recovered on October 26)."""
    runs = sorted((Path.home() / repo / "runs").glob("*/transcript.jsonl"))
    if not runs:
        pytest.skip(f"{repo} is not checked out at ~/{repo}")
    for transcript in runs:
        run = transcript.parent
        text = transcript.read_text()
        final, problem = runners.codex_final(text) if run.name.startswith("sol") else (runners.parse_stream(text)[0],
                                                                                     None)
        recovered = "recovered" in json.loads((run / "meta.json").read_text())
        assert problem is None, run.name
        assert (run / "response.md").read_text() == final + ("" if recovered else "\n"), run.name
    assert any(r.parent.name.startswith("sol") for r in runs)


def _lost_sol(area, monkeypatch, transcript, lane="codex-2", state="lost", show_rc=0):
    run_dir = runners.prepare("sol-01", area.prompt, "sha", "subfleet")
    runners.record(run_dir, job_id="MOCK-sol-01")
    attempt = runners.JOBS / "MOCK-sol-01" / "a1"
    attempt.mkdir(parents=True)
    (attempt / "stdout").write_text(transcript)
    (attempt / "exit.json").write_text(json.dumps({"rc": 0, "signal": None}))
    (attempt / "prompt.sent.md").write_text("MOCK sent prompt\n")
    info = {"job": {"state": state, "finished_at": "2026-11-09T00:00:00Z"},
            "attempts": [{"attempt_id": "MOCK-sol-01/a1", "state": "quarantined", "lane_id": lane,
                          "model_served": "gpt-6.1-sol", "quarantine_reason": "MOCK writers remain after exit receipt"}]}

    def fake(cmd, **kw):
        assert cmd == ["subfleet", "runs", "show", "MOCK-sol-01", "--json"], cmd
        return SimpleNamespace(returncode=show_rc, stdout=json.dumps(info), stderr="MOCK down" if show_rc else "")

    monkeypatch.setattr(runners.subprocess, "run", fake)
    return run_dir, attempt


def test_mock_a_lost_sol_run_whose_turn_completed_is_recovered_and_collected(area, monkeypatch):
    run_dir, attempt = _lost_sol(area, monkeypatch, codex_transcript())
    assert cli.cmd_recover(argparse.Namespace(label="sol-01")) == 0
    meta = runners.read_meta(run_dir)
    assert meta["state"] == "succeeded" and meta["model_served"] == "gpt-6.1-sol"
    assert meta["recovered"]["format"] == "codex"
    assert runners.seal(run_dir)
    assert (run_dir / "response.md").read_text() == "MOCK Sol final"
    assert (run_dir / "transcript.jsonl").read_text() == (attempt / "stdout").read_text()


def test_mock_a_recovered_sol_transcript_changed_afterwards_is_not_collected(area, monkeypatch):
    run_dir, attempt = _lost_sol(area, monkeypatch, codex_transcript())
    runners.recover("sol-01")
    (attempt / "stdout").write_text(codex_transcript("MOCK REPLACED final"))
    assert not runners.seal(run_dir)
    assert "changed" in runners.read_meta(run_dir)["collect_error"]


@pytest.mark.parametrize("transcript,lane,state", [
    (codex_transcript(turns=2), "codex-2", "lost"), (codex_transcript(failed=True), "codex-2", "lost"),
    (codex_transcript(final="  "), "codex-2", "lost"), (CLAUDE_STREAM, "codex-2", "lost"),
    (codex_transcript(), "codex-2", "succeeded")])
def test_mock_a_sol_run_with_nothing_to_recover_exits_2(area, monkeypatch, transcript, lane, state):
    run_dir, _ = _lost_sol(area, monkeypatch, transcript, lane=lane, state=state)
    assert cli.cmd_recover(argparse.Namespace(label="sol-01")) == 2
    assert not runners.read_meta(run_dir).get("recovered")
    assert cli.cmd_recover(argparse.Namespace(label="MOCK-no-such-run")) == 2


def test_mock_a_subfleet_error_during_recovery_is_left_for_a_later_try(area, monkeypatch):
    _lost_sol(area, monkeypatch, codex_transcript(), show_rc=1)
    with pytest.raises(RuntimeError) as raised:
        cli.cmd_recover(argparse.Namespace(label="sol-01"))
    assert not isinstance(raised.value, runners.Unrecoverable)


# --- Findings 1, 3 and 8: scripts/seal.py ------------------------------------------------------------

RECORDS = {"check": "response.json", "audit": "audit.json", "audit_codex_sessions.py": "codex-sessions/audit.json"}


class Commands:
    """Every command scripts/seal.py runs, faked. Each returns `rc[step]` (0 unless set) and `out[step]`;
    `prereg` writes a manifest as `manifest` says: "fresh" lists every answer, "short" leaves one out,
    "none" writes nothing, as when prereg dies before hashing. Its proof appears only when it exits 0.
    `i86 check`, `i86 audit` and the session audit write their record for each collected answer they
    cover, as the real steps do, unless the test puts the step in `silent`, as when it crashes."""

    def __init__(self, root: Path):
        self.root, self.calls, self.rc, self.out, self.timeouts = root, [], {}, {}, {}
        self.manifest, self.pushed, self.silent = "fresh", False, set()

    def record(self, step: str) -> None:
        for d in sorted((self.root / "runs").glob("*")):
            if not (d / "response.md").exists() or not (d / "meta.json").exists():
                continue
            meta = json.loads((d / "meta.json").read_text())
            if (step == "audit" and meta.get("backend") == "manual") or (
                    step == "audit_codex_sessions.py" and not runners.served_by_codex(meta)):
                continue
            path = d / RECORDS[step]
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps({"MOCK": step}) + "\n")

    def __call__(self, cmd, timeout=600):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        self.timeouts[" ".join(cmd[:2]) if not cmd[0].endswith(("/i86", "/python")) else
                      (cmd[1] if cmd[0].endswith("/i86") else Path(cmd[1]).name)] = timeout
        step = (cmd[1] if cmd[0].endswith("/i86") else Path(cmd[1]).name if cmd[0].endswith("/python")
                else " ".join(cmd[:2]))
        rc = self.rc.get(step, 0)
        if step in RECORDS and step not in self.silent:
            self.record(step)
        if step == "prereg":
            sums, proof = self.root / "prereg" / "SHA256SUMS", self.root / "prereg" / "SHA256SUMS.ots"
            proof.unlink(missing_ok=True)
            if self.manifest != "none":
                answers = sorted(str(p.relative_to(self.root)) for p in (self.root / "runs").glob("*/response.md"))
                if self.manifest == "short":
                    answers = answers[1:]
                sums.write_text("".join(f"MOCK  {p}\n" for p in ["README.md", *answers]))
                if rc == 0:
                    proof.write_text("MOCK proof\n")
        if step == "git push":
            self.pushed = rc == 0
        return SimpleNamespace(returncode=rc, stdout=self.out.get(step, ""), stderr="")

    def message(self) -> str:
        commit = next(c for c in self.calls if c[:2] == ["git", "commit"])
        return commit[commit.index("-m") + 1]


@pytest.fixture
def sealing(tmp_path, monkeypatch):
    """A MOCK repository for scripts/seal.py: no runs yet, two assigned labels, a registration-time
    manifest an hour old, and every command faked."""
    runs, prereg = tmp_path / "runs", tmp_path / "prereg"
    runs.mkdir()
    prereg.mkdir()
    (prereg / "README.md").write_text("| Folder | Written | Covers |\n|---|---|---|\n| `registration/` | x | y |\n")
    sums = prereg / "SHA256SUMS"
    sums.write_text("MOCK  README.md\n")
    old = time.time() - 3600
    os.utime(sums, (old, old))
    for name, value in [("ROOT", tmp_path), ("RUNS", runs), ("PREREG", prereg), ("FOLDER", prereg / "collected"),
                        ("MARKER", tmp_path / "cache" / "sealing"), ("KILL_AT", "2999-01-01T00:00:00Z")]:
        monkeypatch.setattr(seal, name, value)
    monkeypatch.setattr(seal, "assigned", lambda: ["opus-01", "sol-01"])
    states: dict[str, str | None] = {}
    monkeypatch.setattr(seal, "job_state", lambda job_id, give_up=None: states.get(job_id))
    monkeypatch.setattr(seal, "RETRY_S", 0)
    commands = Commands(tmp_path)
    monkeypatch.setattr(seal, "run", commands)
    monkeypatch.setattr(seal, "git", lambda *a: SimpleNamespace(returncode=1, stdout="", stderr=""))  # staged changes
    monkeypatch.setattr(seal, "sealed", lambda: seal.FOLDER.exists() and commands.pushed)
    monkeypatch.setattr(seal.time, "sleep", lambda s: pytest.fail("the test would wait a minute"))
    return SimpleNamespace(runs=runs, prereg=prereg, states=states, commands=commands)


def _run(area, label, state="succeeded", answer=True):
    d = area.runs / label
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"label": label, "backend": "subfleet", "job_id": f"MOCK-{label}",
                                             "state": state}))
    if answer:
        (d / "response.md").write_text("MOCK answer\n")
    return d


EARLY = ["--act-after", "2000-01-01T00:00:00Z", "--give-up", "2999-01-01T00:00:00Z"]


def _soon(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_before_the_kill_time_every_assigned_label_not_yet_dispatched_is_pending(sealing, monkeypatch):
    monkeypatch.setattr(seal, "KILL_AT", "2026-11-10T12:00:00Z")
    before, after = (datetime(2026, 11, d, 12, tzinfo=timezone.utc) for d in (8, 10))
    assert seal.pending(before) == {"opus-01": "not dispatched", "sol-01": "not dispatched"}
    assert seal.pending(after) == {}
    _run(sealing, "opus-01")
    assert seal.pending(before) == {"sol-01": "not dispatched"}


def test_the_fallback_seals_nothing_before_the_first_dispatch(sealing, monkeypatch):
    monkeypatch.setattr(seal, "seal", lambda who, last_call=False, give_up=None: pytest.fail("sealed with no run dispatched"))
    assert seal.main(["--once", *EARLY]) == 0
    assert seal.main(["--now"]) == 1
    assert not seal.FOLDER.exists()


def test_a_clean_pass_seals_the_manifest_and_its_proof(sealing):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01")
    assert seal.seal("MOCK") == 0
    assert sorted(p.name for p in seal.FOLDER.iterdir()) == ["SHA256SUMS", "SHA256SUMS.ots"]
    assert "stamped with OpenTimestamps" in sealing.commands.message()
    assert "gaps" not in sealing.commands.message()


def test_a_lost_status_subfleet_does_not_give_stops_the_pass_until_the_last_call(sealing):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01", state="lost", answer=False)  # sealing.states gives no state for it
    assert seal.seal("MOCK") == 1 and not seal.FOLDER.exists()
    assert seal.seal("MOCK", last_call=True) == 0
    assert "sol-01" in (seal.FOLDER / "GAPS.txt").read_text()
    assert "Sealed at the last call, with gaps:\nSubfleet gave no state at the last call for sol-01: abandoned" \
        in sealing.commands.message()
    assert [str(seal.I86), "abandon", "sol-01", "--why", seal.UNKNOWN_AT_LAST_CALL] in sealing.commands.calls


@pytest.mark.parametrize("rc,seals", [(0, True), (2, True), (1, False)])
def test_a_recovery_that_a_later_try_may_mend_stops_the_pass(sealing, rc, seals):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01", state="lost", answer=False)
    sealing.states["MOCK-sol-01"] = "lost"
    sealing.commands.rc["recover"] = rc
    assert (seal.seal("MOCK") == 0) == seals and seal.FOLDER.exists() == seals
    assert any(c[1:] == ["recover", "sol-01"] for c in sealing.commands.calls)


def test_a_collect_that_left_an_answer_out_stops_the_pass_until_the_last_call(sealing):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01", answer=False)
    sealing.commands.rc["collect"] = 1
    assert seal.seal("MOCK") == 1 and not seal.FOLDER.exists()
    assert seal.seal("MOCK", last_call=True) == 0
    assert "collect could not copy the answers of sol-01" in (seal.FOLDER / "GAPS.txt").read_text()


def test_a_run_still_going_never_seals_even_at_the_last_call(sealing):
    _run(sealing, "opus-01")
    sealing.commands.out["collect"] = "1 run(s) still going (sol-01); no answers copied yet."
    for last_call in (False, True):
        assert seal.seal("MOCK", last_call=last_call) == 1
    assert not seal.FOLDER.exists()


def test_a_failed_stamp_stops_the_pass_and_the_last_call_seals_the_hashes_alone(sealing):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01")
    sealing.commands.rc["prereg"] = 1
    assert seal.seal("MOCK") == 1 and not seal.FOLDER.exists()
    assert "collected" not in (sealing.prereg / "README.md").read_text()
    assert seal.seal("MOCK", last_call=True) == 0
    assert sorted(p.name for p in seal.FOLDER.iterdir()) == ["GAPS.txt", "SHA256SUMS"]
    assert "OpenTimestamps stamp FAILED; hashes only" in sealing.commands.message()


@pytest.mark.parametrize("manifest", ["none", "short"])
def test_a_manifest_that_is_stale_or_misses_an_answer_never_seals(sealing, manifest):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01")
    sealing.commands.manifest = manifest
    for last_call in (False, True):
        assert seal.seal("MOCK", last_call=last_call) == 1
    assert not seal.FOLDER.exists()


def test_the_fallback_stops_at_the_give_up_time_with_the_folder_unpushed(sealing):
    seal.FOLDER.mkdir()
    assert seal.main(["--act-after", "2000-01-01T00:00:00Z", "--give-up", "2000-01-02T00:00:00Z"]) == 1


def test_the_last_call_starts_30_minutes_before_giving_up(sealing, monkeypatch):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01")
    calls = []
    monkeypatch.setattr(seal, "seal", lambda who, last_call=False, give_up=None: calls.append(last_call) or 1)
    for argv in (["--once", "--act-after", "2000-01-01T00:00:00Z", "--give-up", _soon(120)],
                 ["--once", "--act-after", "2000-01-01T00:00:00Z", "--give-up", _soon(10)],
                 ["--now"], ["--now", "--last-call"]):
        seal.main(argv)
    assert calls == [False, True, False, True]


def test_at_the_last_call_a_run_still_going_gets_one_pass_of_the_guard(sealing, monkeypatch):
    _run(sealing, "opus-01")
    _run(sealing, "sol-01", state="running", answer=False)
    sealing.states["MOCK-sol-01"] = "running"
    monkeypatch.setattr(seal, "seal", lambda who, last_call=False, give_up=None: pytest.fail("sealed while a run was going"))
    guard_pass = [str(seal.PYTHON), str(seal.GUARD), "--once"]
    assert seal.main(["--once", *EARLY]) == 0
    assert guard_pass not in sealing.commands.calls
    assert seal.main(["--once", "--act-after", "2000-01-01T00:00:00Z", "--give-up", _soon(10)]) == 0
    assert sealing.commands.calls.count(guard_pass) == 1


# --- Findings 1 and 4: scripts/deadline_guard.py ----------------------------------------------------

class Waited(Exception):
    """The guard went to sleep for a minute: it did not exit."""


@pytest.fixture
def guarding(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(guard, "RUNS", runs)
    monkeypatch.setattr(guard, "assigned", lambda: ["opus-01", "sol-01"])
    out = SimpleNamespace(runs=runs, stopped=[], abandoned=[], why=[], asked=[], states={})
    monkeypatch.setattr(guard, "stop", lambda label, job_id, reason: out.stopped.append(label))
    monkeypatch.setattr(guard, "abandon", lambda label, why: out.abandoned.append(label) or out.why.append(why))
    monkeypatch.setattr(guard, "job_state", lambda job_id: out.asked.append(job_id) or out.states.get(job_id))

    def sleep(seconds):
        raise Waited

    monkeypatch.setattr(guard.time, "sleep", sleep)
    return out


FUTURE = ["--start-by", "2999-01-01T00:00:00Z", "--kill-at", "2999-01-01T03:00:00Z"]
PAST = ["--start-by", "2000-01-01T00:00:00Z", "--kill-at", "2000-01-01T03:00:00Z"]


def _guard_run(area, label, **meta):
    d = area.runs / label
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"label": label, "backend": "subfleet", **meta}))


def test_the_guard_keeps_guarding_until_every_assigned_label_has_gone_out(guarding):
    with pytest.raises(Waited):
        guard.main(FUTURE)  # nothing dispatched yet: launchd may start it before the first dispatch
    _guard_run(guarding, "opus-01", job_id="MOCK-1", state="succeeded")
    with pytest.raises(Waited):
        guard.main(FUTURE)  # sol-01 has not gone out
    assert guard.main(PAST) == 0  # past the kill time, a label never dispatched cannot finish in time


def test_the_guard_never_stops_a_run_on_a_state_subfleet_did_not_give(guarding):
    _guard_run(guarding, "opus-01", job_id="MOCK-1", state="running")
    for argv in (FUTURE, PAST):
        with pytest.raises(Waited):
            guard.main(argv)
    assert guarding.stopped == [] and guarding.abandoned == []
    guarding.states["MOCK-1"] = "running"
    assert guard.main(["--once", *PAST]) == 0 and guarding.stopped == ["opus-01"]


def test_the_guard_leaves_alone_a_run_that_collect_recorded_as_finished(guarding):
    for label in ("opus-01", "sol-01"):
        _guard_run(guarding, label, job_id=f"MOCK-{label}", state="succeeded")
    assert guard.main(PAST) == 0
    assert guarding.asked == [] and guarding.stopped == [] and guarding.abandoned == []


def test_the_kill_time_is_one_time_in_the_exclusions_the_seal_and_the_guard(tmp_path, monkeypatch, capsys):
    assert seal.KILL_AT == cli.DEADLINE
    monkeypatch.setattr(guard, "RUNS", tmp_path)
    monkeypatch.setattr(guard, "assigned", lambda: [])
    assert guard.main(["--once"]) == 0
    assert f"kill at {cli.DEADLINE}" in capsys.readouterr().out


# --- Findings 6 and 15: the run's own work directory, and its neighbours -----------------------------

WORK = runners.WORK / "opus-1"
TAIL = "/" + WORK.relative_to(HOME).as_posix()
SPELLINGS = ["~", "$HOME", "${HOME}", '"$HOME"', '"${HOME}"', '\\"$HOME\\"', str(HOME)]


@pytest.mark.parametrize("after", [";", "|", "&", "(", ")", "<", ">", "`", " ", "\t", "'", '"', "\\", "/x", ""])
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_the_runs_own_directory_is_masked_before_anything_that_ends_its_name(spelling, after):
    assert _mask_workdir(f"x {spelling}{TAIL}{after}", WORK) == f"x <workdir>{after}"


@pytest.mark.parametrize("after", list("0123456789azAZ._-*?[{"))
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_name_that_goes_on_is_another_directory_and_stays(spelling, after):
    assert "<workdir>" not in _mask_workdir(f"x {spelling}{TAIL}{after}", WORK)


def _bash(tmp_path, command):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}) + "\n")
    return audit_transcript(path, str(WORK))


@pytest.mark.parametrize("command", [f"cd ~{TAIL}; ls", f"ls ~{TAIL}|head", f"echo $(ls ~{TAIL})",
                                     f'cd "$HOME"{TAIL} && ls', f"cat ~{TAIL}/..notes"])
def test_a_run_that_names_its_own_directory_this_way_is_no_violation(tmp_path, command):
    assert _bash(tmp_path, command)["violations"] == []


@pytest.mark.parametrize("command", [f"ls ~{TAIL}/..", f"cat ~{TAIL}/../opus-2/notes.md", f"ls {WORK}/..",
                                     f"cat ~{TAIL}1/notes.md"])
def test_the_parent_through_dots_or_a_sibling_stays_a_violation(tmp_path, command):
    out = _bash(tmp_path, command)
    assert out["violations"] and not out["passed"]


# --- Finding 14: the launchd jobs ---------------------------------------------------------------------

@pytest.mark.parametrize("name,script", [("com.maxghenis.aksen-seal", "seal.py"),
                                         ("com.maxghenis.aksen-deadline", "deadline_guard.py")])
def test_each_launchd_job_runs_its_script_once_at_load_with_the_tools_it_calls(name, script):
    job = plistlib.loads((ROOT / "launchd" / f"{name}.plist").read_bytes())
    where = Path(job["WorkingDirectory"])
    assert job["Label"] == name
    assert job["ProgramArguments"] == [str(where / ".venv" / "bin" / "python"), str(where / "scripts" / script)]
    assert job["RunAtLoad"] is True
    assert not {"KeepAlive", "StartInterval", "StartCalendarInterval", "WatchPaths"} & set(job)
    assert job["StandardOutPath"] == job["StandardErrorPath"] == str(where / ".diag" / f"launchd-{name.split('-')[-1]}.log")
    if where != ROOT:
        pytest.skip("the jobs name the dispatching Mac's checkout")
    for tool in ("subfleet", "git", "uvx"):
        assert shutil.which(tool, path=job["EnvironmentVariables"]["PATH"]), tool
