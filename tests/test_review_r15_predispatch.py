"""Fixes from the pre-dispatch review of 2026-11-08. MOCK transcripts only."""

import json

import pytest

from i86 import runners
from i86.audit import HOME, _mask_workdir, audit_transcript

WORK = runners.WORK / "opus-1"


def claude(name, **inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


@pytest.mark.parametrize("spelling", ["~", "$HOME", "${HOME}", str(HOME)])
def test_every_spelling_of_the_runs_own_work_directory_is_masked(spelling):
    """Note 1: the work directory's name holds a protected term, so a run that wrote its own directory
    as ~/... (or $HOME/..., ${HOME}/...) failed its audit as naming a protected place."""
    tail = "/" + WORK.relative_to(HOME).as_posix()
    assert _mask_workdir(f"cat {spelling}{tail}/notes.md", WORK) == "cat <workdir>/notes.md"
    assert _mask_workdir(f"ls {spelling}{tail}", WORK) == "ls <workdir>"
    for other in (f"{spelling}{tail}0/x", f"{spelling}{tail}x", f"{spelling}/.cache/aksen-work"):
        assert "<workdir>" not in _mask_workdir(f"cat {other}", WORK)


@pytest.mark.parametrize("command", ["cat ~/.cache/aksen-work/opus-1/notes.md",
                                     "cat $HOME/.cache/aksen-work/opus-1/notes.md",
                                     "echo MOCK > ${HOME}/.cache/aksen-work/opus-1/notes.md"])
def test_a_run_that_names_its_own_directory_from_home_is_no_violation(tmp_path, command):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps(claude("Bash", command=command)) + "\n")
    out = audit_transcript(path, str(WORK))
    assert out["violations"] == []


@pytest.mark.parametrize("command", ["ls ~/.cache/aksen-work", "cat ~/.cache/aksen-work/opus-10/notes.md",
                                     "cat $HOME/.cache/aksen-staging/opus-1/response.md"])
def test_a_sibling_or_the_parent_stays_a_violation(tmp_path, command):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps(claude("Bash", command=command)) + "\n")
    out = audit_transcript(path, str(WORK))
    assert out["violations"] and not out["passed"]
