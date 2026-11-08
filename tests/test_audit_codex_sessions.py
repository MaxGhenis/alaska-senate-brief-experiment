"""scripts/audit_codex_sessions.py finds every Codex thread behind a run, keeps its log and audits it.
MOCK session logs in temporary directories."""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "audit_codex_sessions", Path(__file__).parent.parent / "scripts" / "audit_codex_sessions.py")
acs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acs)

PARENT, HELPER = "MOCK-parent-thread", "MOCK-helper-thread"


def write_log(lanes: Path, name: str, meta: dict, items=(), records=()) -> Path:
    path = lanes / "codex-9" / "sessions" / "2026" / "11" / "03" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "session_meta", "payload": meta}, *records]
    lines += [{"type": "event_msg", "payload": {"type": "item_completed", "item": it}} for it in items]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


def started(thread: str) -> dict:
    return {"type": "SubAgentActivity", "kind": "started", "agent_thread_id": thread}


def search(query: str) -> dict:
    return {"type": "Extension", "kind": "web.search", "id": f"MOCK-{query}", "query": query,
            "action": {"type": "search", "queries": [query]}, "results": []}


def command(cmd: str, cwd: str) -> dict:
    return {"type": "CommandExecution", "id": f"MOCK-{cmd}", "command": ["/bin/zsh", "-lc", cmd],
            "cwd": f"file://{cwd}", "status": "completed", "exit_code": 0, "aggregated_output": ""}


def helper_meta(workdir: str, thread: str = HELPER) -> dict:
    return {"id": thread, "cwd": workdir, "timestamp": "2026-11-03T18:10:30Z",
            "source": {"subagent": {"thread_spawn": {"parent_thread_id": PARENT, "depth": 1,
                                                     "agent_path": "/root/MOCK_research"}}}}


@pytest.fixture
def area(tmp_path, monkeypatch):
    """A MOCK collected Codex run, its main thread's log, and a log from an unrelated job."""
    workdir = str(tmp_path / "work" / "sol-1")
    Path(workdir).mkdir(parents=True)
    run = tmp_path / "runs" / "sol-1"
    run.mkdir(parents=True)
    (run / "meta.json").write_text(json.dumps({"label": "sol-1", "workdir": workdir, "lane_id": "codex-9"}))
    (run / "response.md").write_text("MOCK answer\n")
    monkeypatch.setattr(acs, "RUNS", tmp_path / "runs")
    lanes = tmp_path / "lanes"
    write_log(lanes, "rollout-MOCK-other.jsonl", {"id": "MOCK-other", "cwd": str(tmp_path / "elsewhere"),
                                                  "source": "exec", "timestamp": "2026-11-03T18:00:00Z"},
              items=[command("cat /etc/hosts", str(tmp_path / "elsewhere"))])
    return lanes, run, workdir


def add_parent(lanes: Path, workdir: str, spawned=(HELPER,)) -> Path:
    return write_log(lanes, "rollout-MOCK-parent.jsonl",
                     {"id": PARENT, "cwd": workdir, "source": "exec", "timestamp": "2026-11-03T18:10:00Z"},
                     items=[search("MOCK main query"), *[started(t) for t in spawned]])


def test_the_main_thread_and_its_sub_agents_are_kept_and_audited(area):
    lanes, run, workdir = area
    add_parent(lanes, workdir)
    write_log(lanes, "rollout-MOCK-helper.jsonl", helper_meta(workdir),
              items=[search("MOCK helper query"), command("echo MOCK", workdir)])
    report = acs.audit_run(run, lanes)
    assert {t["thread_id"] for t in report["threads"]} == {PARENT, HELPER}
    assert report["missing"] == [] and report["flags"] == [] and report["passed"]
    assert report["web_calls"] == 2 and report["commands"] == 1
    assert (run / "codex-sessions" / "rollout-MOCK-helper.jsonl").is_file()
    assert not (run / "codex-sessions" / "rollout-MOCK-other.jsonl").exists()
    assert json.loads((run / "codex-sessions" / "audit.json").read_text())["passed"]


def test_a_spawned_thread_with_no_log_fails(area):
    lanes, run, workdir = area
    add_parent(lanes, workdir, spawned=(HELPER, "MOCK-lost-thread"))
    write_log(lanes, "rollout-MOCK-helper.jsonl", helper_meta(workdir), items=[search("MOCK helper query")])
    report = acs.audit_run(run, lanes)
    assert report["missing"] == ["MOCK-lost-thread"] and not report["passed"]


def test_a_sub_agent_reading_the_repository_fails_the_audit(area):
    lanes, run, workdir = area
    add_parent(lanes, workdir)
    write_log(lanes, "rollout-MOCK-helper.jsonl", helper_meta(workdir),
              items=[command(f"cat {acs.ROOT / 'README.md'}", workdir)])
    report = acs.audit_run(run, lanes)
    helper = next(t for t in report["threads"] if t["thread_id"] == HELPER)
    assert helper["audit"]["violations"] and not report["passed"]


def test_tools_and_places_the_script_does_not_read_are_flagged(area):
    lanes, run, workdir = area
    add_parent(lanes, workdir)
    records = [{"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                                                     "input": "text(await tools.read_file({path: 'MOCK'}))"}},
               {"type": "response_item", "payload": {"type": "function_call", "name": "MOCK_shell"}}]
    write_log(lanes, "rollout-MOCK-helper.jsonl", helper_meta(workdir), records=records,
              items=[command("echo MOCK", "/MOCK/elsewhere"), {"type": "MOCKItem", "id": "MOCK"}])
    report = acs.audit_run(run, lanes)
    flags = " ".join(report["flags"])
    for expected in ("tools.read_file", "'MOCK_shell'", "outside the work directory", "'MOCKItem'"):
        assert expected in flags
    assert not report["passed"]


def test_a_rerun_after_the_lane_prunes_its_logs_audits_the_kept_copies(area):
    lanes, run, workdir = area
    parent = add_parent(lanes, workdir)
    helper = write_log(lanes, "rollout-MOCK-helper.jsonl", helper_meta(workdir), items=[search("MOCK helper query")])
    assert acs.audit_run(run, lanes)["passed"]
    first = (run / "codex-sessions" / "audit.json").read_bytes()
    parent.unlink()
    helper.unlink()
    assert acs.audit_run(run, lanes)["passed"]
    assert (run / "codex-sessions" / "audit.json").read_bytes() == first


def test_a_kept_log_is_final(area):
    lanes, run, workdir = area
    parent = add_parent(lanes, workdir, spawned=())
    assert acs.audit_run(run, lanes)["passed"]
    with parent.open("a") as fh:
        fh.write(json.dumps({"type": "event_msg", "payload": {"type": "token_count"}}) + "\n")
    with pytest.raises(SystemExit, match="the kept copy is final"):
        acs.audit_run(run, lanes)
