"""scripts/deviation_1.py: the registered analysis, also pooling Sol runs whose session audit failed on
exec-script flags alone. Every run, forecast and audit here is invented MOCK data.

Invariants:
- `admit` changes a record only by setting `passed` to True, and only for an admitted label whose
  audit failed with at least one flag, every flag an exec script's, no missing or orphaned thread, no
  violation, and at least one thread, each of whose own audits passed; any other failed record of an
  admitted label stops the script, and every record of another label passes through unchanged
  (property test over generated records);
- on runs whose session audits all pass, the deviation's outputs equal scripts/arms.py's byte for byte,
  apart from the summary's deviation note (differential test);
- a run whose session audit failed on an exec-script flag stays out of scripts/arms.py's pool and
  enters the deviation's when admitted, and only then;
- the registered `cli._session_audit` is back in place after the script returns or fails.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from i86 import cli

from test_arms_script import arms_script, mock_runs, read  # noqa: F401  (mock_runs is a fixture)

spec = importlib.util.spec_from_file_location("deviation_1", Path(__file__).parent.parent / "scripts" / "deviation_1.py")
deviation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deviation)

EXEC = f"rollout-MOCK.jsonl line 1: {deviation.EXEC_FLAG}: 'MOCK const [n,fs]=...; fs.all'"
OTHER = "rollout-MOCK.jsonl line 2: a command ran in /MOCK, outside the work directory"
DRAWS = ["--draws", "300", "--secondary-draws", "50"]

# Each way a failed session audit can differ from one that failed on exec scripts alone. The strategy
# below builds such a record and then breaks a chosen set of these conditions, so every single break,
# the boundary the deviation must hold, comes up often (and once each, below, as an explicit example).
BREAKS = ("no flag", "other flag", "missing", "orphans", "violations", "no thread", "failed thread")


def build(passed: bool, breaks: frozenset, exec_flags: int, good_threads: int) -> dict:
    flags = [] if "no flag" in breaks else [EXEC] * exec_flags
    threads = [] if "no thread" in breaks else [{"audit": {"passed": True, "suspect": []}}] * good_threads
    return {"passed": passed,
            "flags": flags + ([OTHER] if "other flag" in breaks else []),
            "threads": threads + ([{"audit": {"passed": False, "suspect": []}}] if "failed thread" in breaks else []),
            "missing": ["MOCK-thread"] if "missing" in breaks else [],
            "orphans": ["MOCK-thread"] if "orphans" in breaks else [],
            "violations": 1 if "violations" in breaks else 0}


records = st.builds(build, passed=st.booleans(), breaks=st.frozensets(st.sampled_from(BREAKS), max_size=3),
                    exec_flags=st.integers(min_value=1, max_value=3), good_threads=st.integers(min_value=1, max_value=3))


@settings(max_examples=500, deadline=None)
@given(rec=records, admitted=st.booleans())
@example(rec=build(False, frozenset(), 1, 1), admitted=True)
@example(rec=build(True, frozenset(), 1, 1), admitted=True)
@example(rec=build(False, frozenset(), 1, 1), admitted=False)
@example(rec=build(False, frozenset({"other flag"}), 0, 1), admitted=True)  # a non-exec flag alone
@example(rec=build(False, frozenset({"no thread", "failed thread"}), 1, 1), admitted=True)  # a failed thread alone
@example(rec=build(False, frozenset({"no flag", "other flag"}), 1, 1), admitted=True)
def test_mock_admit_passes_only_audits_that_failed_on_exec_scripts_alone(rec, admitted):
    before = json.loads(json.dumps(rec))
    eligible = (not rec["passed"] and bool(rec["flags"]) and all(f == EXEC for f in rec["flags"])
                and not rec["missing"] and not rec["orphans"] and not rec["violations"]
                and bool(rec["threads"]) and all(t["audit"]["passed"] for t in rec["threads"]))
    label, chosen = "sol-MOCK", {"sol-MOCK"} if admitted else set()
    if admitted and not rec["passed"] and not eligible:
        with pytest.raises(SystemExit):
            deviation.admit(rec, label, chosen)
        assert rec == before
        return
    out = deviation.admit(rec, label, chosen)
    assert rec == before
    if admitted and eligible:
        assert out["passed"] is True and {k: v for k, v in out.items() if k != "passed"} == {
            k: v for k, v in rec.items() if k != "passed"}
    else:
        assert out is rec


@pytest.mark.parametrize("broken", BREAKS)
def test_mock_admit_refuses_each_single_break(broken):
    rec = build(False, frozenset({broken}), 1, 1)
    with pytest.raises(SystemExit, match="sol-MOCK"):
        deviation.admit(rec, "sol-MOCK", {"sol-MOCK"})


@pytest.mark.parametrize("rec", [None, "unreadable"])
def test_mock_admit_leaves_a_missing_or_unreadable_audit_alone(rec):
    assert deviation.admit(rec, "sol-01", {"sol-01"}) is rec


def fail_session(mock_runs, label: str, flag: str) -> None:
    path = mock_runs / "runs" / label / "codex-sessions" / "audit.json"
    path.write_text(json.dumps({"passed": False, "violations": 0, "missing": [], "orphans": [], "flags": [flag],
                                "threads": [{"audit": {"passed": True, "suspect": []}}]}))


def pooled(out: Path) -> set[str]:
    return {row["run"] for row in read(out / "per_run.csv")}


def test_mock_matches_the_registered_script_when_every_session_audit_passes(mock_runs):
    reg, dev = mock_runs / "reg", mock_runs / "dev"
    assert arms_script.main([*DRAWS, "--out", str(reg)]) == 0
    assert deviation.main(["--admit", "sol-01", *DRAWS, "--out", str(dev)]) == 0
    for name in ("per_run.csv", "arm_means.csv", "effects.csv", "primary_by_family.csv"):
        assert (dev / name).read_bytes() == (reg / name).read_bytes(), name
    reg_lines, dev_lines = (reg / "summary.md").read_text().split("\n"), (dev / "summary.md").read_text().split("\n")
    assert dev_lines[1] == "" and dev_lines[2].startswith("Deviation 1 (prereg/README.md), not the registered analysis")
    assert [dev_lines[0], *dev_lines[3:]] == reg_lines
    assert cli._session_audit is deviation.arms_registered.cli._session_audit


def test_mock_admits_a_run_the_session_audit_failed_on_an_exec_script(mock_runs):
    fail_session(mock_runs, "sol-04", EXEC)
    reg, dev, none = mock_runs / "reg", mock_runs / "dev", mock_runs / "none"
    assert arms_script.main([*DRAWS, "--out", str(reg)]) == 0
    assert "sol-04" not in pooled(reg) and len(pooled(reg)) == 23
    assert deviation.main(["--admit", "sol-04", *DRAWS, "--out", str(dev)]) == 0
    assert pooled(dev) == pooled(reg) | {"sol-04"}
    assert deviation.main(["--admit", "sol-05", *DRAWS, "--out", str(none)]) == 0
    assert pooled(none) == pooled(reg)


@pytest.mark.parametrize("flag", [OTHER, "1 threads started by codex exec in the work directory; expected 1"])
def test_mock_refuses_a_run_that_failed_on_anything_else(mock_runs, flag):
    fail_session(mock_runs, "sol-04", flag)
    registered = cli._session_audit
    with pytest.raises(SystemExit, match="sol-04"):
        deviation.main(["--admit", "sol-04", *DRAWS, "--out", str(mock_runs / "dev")])
    assert cli._session_audit is registered


def test_mock_needs_a_label(mock_runs):
    with pytest.raises(SystemExit, match="--admit"):
        deviation.main([*DRAWS, "--out", str(mock_runs / "dev")])
