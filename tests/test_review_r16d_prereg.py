"""The fourth pre-registration re-review's items (B1, B2) and the record check that B2 led to, each with
a test. MOCK runs, lanes and Subfleet.

- B1: at the last call, `i86 collect` and `i86 recover` get up to LAST_CALL_TRIES tries while they
  fail, so a Subfleet stall that clears within the last call costs a try, not the answers. A later try
  starts only with RESERVE_S left for the steps after it, and runs no longer than the time left minus
  RESERVE_S. Before the last call, each runs once and a failure stops the pass.
- B2: the session audit keeps only the parts of a log's header that `find_threads` reads, and finds
  the same threads as it would from whole headers.
- B3: before the last call, a pass seals no collected answer that a check or audit left without its
  record (a step that crashed or was killed writes none); at the last call, GAPS.txt names each such
  run and step.
"""

import itertools
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from i86 import runners
from test_review_r16_prereg import RECORDS, _run, seal, sealing  # noqa: F401
from test_audit_codex_sessions import acs, write_log


def _later(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _tries(sealing, monkeypatch, step: str, codes: list[int]) -> list[float]:
    """Make each try of `i86 <step>` exit with the next of `codes`. A collect that exits 0 copies every
    succeeded run's answer, and a recover that exits 0 takes the lost run's answer, as the real ones
    do. Returns each try's time limit, one per try."""
    fake, codes, limits = seal.run, iter(codes), []

    def run(cmd, timeout=600):
        argv = [str(c) for c in cmd]
        if not (argv[0].endswith("/i86") and argv[1] == step):
            return fake(cmd, timeout)
        sealing.commands.calls.append(argv)
        limits.append(timeout)
        rc = next(codes)
        if rc == 0:
            for d in sorted(sealing.runs.iterdir()):
                meta = json.loads((d / "meta.json").read_text())
                if not (d / "response.md").exists() and (
                        (step == "collect" and meta["state"] == "succeeded")
                        or (step == "recover" and d.name == argv[2])):
                    (d / "response.md").write_text("MOCK answer\n")
        return SimpleNamespace(returncode=rc, stdout="", stderr="")

    monkeypatch.setattr(seal, "run", run)
    return limits


def _gaps() -> str:
    path = seal.FOLDER / "GAPS.txt"
    return path.read_text() if path.exists() else ""


def _steps(sealing) -> list[str]:
    return [c[1] if c[0].endswith("/i86") else Path(c[1]).name for c in sealing.commands.calls
            if c[0].endswith(("/i86", "/python"))]


# --- B1: at the last call, collect and recover try again while Subfleet fails them ------------------

def test_mock_b1_a_stall_that_clears_costs_a_try_not_the_answers(sealing, monkeypatch):
    """B1's sequence: both runs succeeded but are not yet copied, and the first collect's Subfleet calls
    stall until its breaker fails the rest (exit 1). The second collect copies both answers."""
    for label in ("opus-01", "sol-01"):
        _run(sealing, label, answer=False)
    limits = _tries(sealing, monkeypatch, "collect", [1, 0])
    assert seal.seal("MOCK", last_call=True, give_up=_later(1800)) == 0
    assert len(limits) == 2
    assert _gaps() == ""
    listed = (seal.FOLDER / "SHA256SUMS").read_text()
    assert "runs/opus-01/response.md" in listed and "runs/sol-01/response.md" in listed


def test_before_the_last_call_a_failed_collect_runs_once_and_stops_the_pass(sealing, monkeypatch):
    _run(sealing, "opus-01", answer=False)
    limits = _tries(sealing, monkeypatch, "collect", [1, 0])
    assert seal.seal("MOCK", give_up=_later(7200)) == 1
    assert len(limits) == 1 and not seal.FOLDER.exists()


def test_at_the_last_call_collect_stops_after_its_last_try_and_gaps_name_the_run(sealing, monkeypatch):
    _run(sealing, "opus-01", answer=False)
    _run(sealing, "sol-01")
    limits = _tries(sealing, monkeypatch, "collect", [1] * 10)
    assert seal.seal("MOCK", last_call=True, give_up=_later(1800)) == 0
    assert len(limits) == seal.LAST_CALL_TRIES == 3
    assert "collect could not copy the answers of opus-01" in _gaps()
    assert "opus-01 (succeeded)" in _gaps()


@pytest.mark.parametrize("spare", [-1, 0, 100])
def test_a_later_try_leaves_the_reserve_for_the_steps_after_it(sealing, monkeypatch, spare):
    _run(sealing, "opus-01", answer=False)
    limits = _tries(sealing, monkeypatch, "collect", [1] * 10)
    assert seal.seal("MOCK", last_call=True, give_up=_later(seal.RESERVE_S + spare)) == 0
    if spare <= 0:
        assert len(limits) == 1
    else:
        assert len(limits) == 3 and all(0 < limit <= spare for limit in limits[1:])
    assert {"check", "audit", "audit_codex_sessions.py", "prereg"} <= set(_steps(sealing))


def test_with_no_give_up_time_a_later_try_gets_collects_own_limit(sealing, monkeypatch):
    _run(sealing, "opus-01", answer=False)
    limits = _tries(sealing, monkeypatch, "collect", [1, 1, 0])
    assert seal.seal("MOCK", last_call=True) == 0
    assert limits == [seal.LIMIT["collect"]] * 3


def test_a_collect_that_says_a_run_is_still_going_is_not_tried_again(sealing, monkeypatch):
    _run(sealing, "opus-01", answer=False)
    fake = seal.run
    collects = []

    def run(cmd, timeout=600):
        argv = [str(c) for c in cmd]
        if argv[0].endswith("/i86") and argv[1] == "collect":
            collects.append(argv)
            return SimpleNamespace(returncode=1, stdout="1 run(s) still going (opus-01)", stderr="")
        return fake(cmd, timeout)

    monkeypatch.setattr(seal, "run", run)
    assert seal.seal("MOCK", last_call=True, give_up=_later(1800)) == 1
    assert len(collects) == 1 and not seal.FOLDER.exists()


@pytest.mark.parametrize("codes", list(itertools.product([0, 1, 124], repeat=3)))
@pytest.mark.parametrize("last_call", [False, True])
def test_collect_tries_until_it_works_only_at_the_last_call(sealing, monkeypatch, codes, last_call):
    """Every sequence of three exits: before the last call, one try; at it, tries until one works, at
    most three. The answer is in the record exactly when a try it got worked."""
    _run(sealing, "opus-01", answer=False)
    _run(sealing, "sol-01")
    limits = _tries(sealing, monkeypatch, "collect", list(codes))
    rc = seal.seal("MOCK", last_call=last_call, give_up=_later(1800))
    worked = codes.index(0) if 0 in codes else None
    allowed = 1 if not last_call else (worked + 1 if worked is not None else seal.LAST_CALL_TRIES)
    assert len(limits) == allowed
    collected = worked is not None and worked < allowed
    assert (sealing.runs / "opus-01" / "response.md").exists() == collected
    if last_call:
        assert rc == 0 and seal.FOLDER.exists()
        assert ("opus-01 (succeeded)" in _gaps()) == (not collected)
    else:
        assert rc == (0 if collected else 1) and seal.FOLDER.exists() == collected


@pytest.mark.parametrize("codes, tries, gap", [([1, 1, 0], 3, False), ([1, 1, 1, 0], 3, True), ([2], 1, False),
                                               ([0], 1, False), ([124, 0], 2, False)])
def test_mock_at_the_last_call_recover_tries_again_while_it_fails(sealing, monkeypatch, codes, tries, gap):
    _run(sealing, "sol-01", state="running", answer=False)
    sealing.states["MOCK-sol-01"] = "lost"
    _run(sealing, "opus-01")
    limits = _tries(sealing, monkeypatch, "recover", codes)
    assert seal.seal("MOCK", last_call=True, give_up=_later(1800)) == 0
    assert len(limits) == tries
    assert ("recovering sol-01 failed" in _gaps()) == gap


def test_before_the_last_call_a_failed_recover_runs_once_and_stops_the_pass(sealing, monkeypatch):
    _run(sealing, "sol-01", state="running", answer=False)
    sealing.states["MOCK-sol-01"] = "lost"
    limits = _tries(sealing, monkeypatch, "recover", [1, 0])
    assert seal.seal("MOCK", give_up=_later(7200)) == 1
    assert len(limits) == 1 and not seal.FOLDER.exists()


# --- B2: the session audit keeps only what it reads of each header ----------------------------------

def test_the_session_audit_keeps_only_the_header_keys_it_reads(tmp_path):
    lanes = tmp_path / "lanes"
    write_log(lanes, "rollout-MOCK-big.jsonl", {"id": "MOCK-big", "cwd": "/MOCK/work", "source": "exec",
                                                "timestamp": "2026-11-03T18:10:00Z",
                                                "base_instructions": {"text": "MOCK " * 200_000}})
    [(path, kept)] = acs.read_headers(acs.lane_logs(lanes))
    assert kept == {"id": "MOCK-big", "cwd": "/MOCK/work", "source": "exec", "timestamp": "2026-11-03T18:10:00Z"}
    assert len(json.dumps(kept)) < 200 < len(json.dumps(acs.header(path)))


IDS = st.sampled_from(["MOCK-a", "MOCK-b", "MOCK-c", "", None, 7])
SOURCES = st.one_of(st.sampled_from(["exec", "vscode", None, 3, [1]]),
                    st.builds(lambda parent, path: {"subagent": {"thread_spawn": {"parent_thread_id": parent,
                                                                                  "agent_path": path}}},
                              IDS, st.sampled_from(["/root/a", None])))
HEADERS = st.fixed_dictionaries(
    {}, optional={"id": IDS, "cwd": st.sampled_from(["/MOCK/work", "/MOCK/other", "", None]), "source": SOURCES,
                  "timestamp": st.sampled_from(["2026-11-03T18:00:00Z", None]),
                  "base_instructions": st.text(max_size=50), "model_provider": st.sampled_from(["openai", 3])})


@settings(max_examples=150, deadline=None)
@given(headers=st.lists(HEADERS, max_size=6))
def test_trimmed_headers_find_the_same_threads_as_whole_ones(headers):
    with tempfile.TemporaryDirectory() as tmp:
        lanes = Path(tmp)
        for i, meta in enumerate(headers):
            write_log(lanes, f"rollout-MOCK-{i:02d}.jsonl", meta)
        paths = acs.lane_logs(lanes)
        whole = acs.find_threads([], "/MOCK/work", [(p, acs.header(p)) for p in paths])
        assert acs.find_threads(paths, "/MOCK/work") == whole


# --- B3: no collected answer goes into the record without its check and audit records ---------------

CODEX = {"lane_id": "codex-2", "model_served": "gpt-6.1-sol"}


def _codex(sealing, label: str):
    d = _run(sealing, label)
    meta = json.loads((d / "meta.json").read_text())
    (d / "meta.json").write_text(json.dumps({**meta, **CODEX}))
    return d


@pytest.mark.parametrize("step, name", [("check", "i86 check"), ("audit", "i86 audit"),
                                        ("audit_codex_sessions.py", "the session audit")])
def test_before_the_last_call_an_answer_a_crashed_step_left_without_its_record_is_not_sealed(sealing, step, name):
    _run(sealing, "opus-01")
    _codex(sealing, "sol-01")
    sealing.commands.silent.add(step)  # the step crashed: it exits 1, as one whose audit failed, and writes nothing
    sealing.commands.rc[step] = 1
    assert seal.seal("MOCK", give_up=_later(7200)) == 1
    assert not seal.FOLDER.exists()
    assert seal.unrecorded() == (["sol-01 (the session audit)"] if step == "audit_codex_sessions.py"
                                 else [f"opus-01 ({name})", f"sol-01 ({name})"])


def test_at_the_last_call_an_answer_without_its_record_is_sealed_and_named(sealing):
    _run(sealing, "opus-01")
    _codex(sealing, "sol-01")
    sealing.commands.silent.add("audit_codex_sessions.py")
    sealing.commands.rc["audit_codex_sessions.py"] = -9  # killed
    assert seal.seal("MOCK", last_call=True, give_up=_later(1800)) == 0
    assert "a check or audit left no record for sol-01 (the session audit)" in _gaps()
    assert "runs/sol-01/response.md" in (seal.FOLDER / "SHA256SUMS").read_text()


def test_steps_that_write_their_records_let_the_pass_seal(sealing):
    _run(sealing, "opus-01")
    _codex(sealing, "sol-01")
    sealing.commands.rc.update({"check": 1, "audit": 1, "audit_codex_sessions.py": 1})  # audits that failed
    assert seal.seal("MOCK", give_up=_later(7200)) == 0
    assert seal.unrecorded() == [] and _gaps() == ""


def test_unrecorded_names_exactly_the_answers_a_step_left_without_its_record(sealing):
    """Every combination of an answer, the three records and the kinds of run, in one folder each."""
    expected = []
    combos = itertools.product([False, True], repeat=7)
    for i, (answer, response, problems, audited, sessions, manual, codex) in enumerate(combos):
        d = sealing.runs / f"r{i:03d}"
        d.mkdir()
        meta = {"label": d.name, "backend": "manual" if manual else "subfleet", "state": "succeeded"}
        (d / "meta.json").write_text(json.dumps({**meta, **(CODEX if codex else {})}))
        for present, record in [(answer, "response.md"), (response, "response.json"), (problems, "problems.txt"),
                                (audited, "audit.json"), (sessions, "codex-sessions/audit.json")]:
            if present:
                (d / record).parent.mkdir(exist_ok=True)
                (d / record).write_text("MOCK\n")
        steps = [step for step, lacking in [("i86 check", not (response or problems)),
                                            ("i86 audit", not manual and not audited),
                                            ("the session audit", codex and not sessions)] if lacking]
        if answer and steps:
            expected.append(f"{d.name} ({', '.join(steps)})")
    (sealing.runs / "r999").mkdir()  # a dispatch starting: no meta.json yet
    assert seal.unrecorded() == expected


@settings(max_examples=300, deadline=None)
@given(meta=st.fixed_dictionaries({}, optional={
    "lane_id": st.one_of(st.sampled_from(["codex-2", "codex", "claude-1", "Codex-2", "", "xcodex"]), st.none(),
                         st.integers()),
    "model_served": st.one_of(st.sampled_from(["gpt-6.1-sol", "gpt", "claude-opus-5-5", "GPT-6", ""]), st.none(),
                              st.integers())}))
def test_seal_decides_a_codex_run_as_runners_does(meta):
    assert seal.served_by_codex(meta) == runners.served_by_codex(meta)


def test_the_fake_steps_write_the_records_the_real_ones_name():
    """The records the seal looks for are the files the steps write (src/i86/cli.py, the session audit)."""
    source = (Path(__file__).resolve().parent.parent / "src" / "i86" / "cli.py").read_text()
    assert 'runners.write_atomic(d / "response.json",' in source and 'runners.write_atomic(d / "problems.txt",' in source
    assert 'runners.write_atomic(d / "audit.json",' in source
    assert acs.FOLDER == seal.SESSIONS and RECORDS["audit_codex_sessions.py"] == f"{seal.SESSIONS}/audit.json"
