"""Deviation 1 (prereg/README.md): the registered analysis, also pooling Sol runs whose session audit
failed only on exec scripts that a blinded reviewer cleared.

    .venv/bin/python scripts/deviation_1.py --admit sol-04 --admit sol-08 --accept-prior opus-02 \
        --out outputs/arms-deviation-1

The session audit (scripts/audit_codex_sessions.py) fails a run when an exec script it ran matches
SCRIPT_ESCAPES, a pattern for code that could reach files, the network or the process past the tools
the audit reads. It failed sol-04 and sol-08 because each script binds a loop variable named `fs`,
whose `fs.all` the pattern's `\\bfs\\.` matched. This script changes no registered file. It runs
scripts/arms.py's `main` as registered, with every other argument passed through, and changes one
thing: for each label given with --admit, it counts the run's session audit as passed, provided the
audit failed on exec-script flags alone, with no missing or orphaned thread, no violation and every
thread's own audit passed. Each flag must also name the script the reviewer read: the session log, the
line and the script's first 200 characters, as outputs/flag-review/ records them (REVIEWED). A run that
fails on anything else stops the script. Every other registered exclusion applies as before, and the
summary says that this is the deviation's analysis.

--out is required, in that spelling or as --out=FOLDER, and the script refuses the folders that hold the
registered analysis and its two sensitivity checks, so it can never write over them.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from i86 import cli  # noqa: E402

_spec = importlib.util.spec_from_file_location("arms_registered", ROOT / "scripts" / "arms.py")
arms_registered = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arms_registered)

EXEC_FLAG = "an exec script reaches past its tools"  # the flag scripts/audit_codex_sessions.py writes

# The exec scripts the blinded reviewer read (outputs/flag-review/README.md): each run's session log, the
# line of that log that holds the script, and the file that keeps the script as the run wrote it.
REVIEW = ROOT / "outputs" / "flag-review"
REVIEWED = {
    "sol-04": ("rollout-2026-11-08T07-44-15-01a1bb30-1da0-7b61-bb0e-a2940be1ab9e.jsonl", 155, "sol-04-script.js"),
    "sol-08": ("rollout-2026-11-08T07-46-17-01a1bb31-faec-7e52-aeab-dac310a4186b.jsonl", 204, "sol-08-script.js"),
}
# The folders of the registered analysis and its two registered sensitivity checks.
REGISTERED_OUT = tuple(ROOT / "outputs" / name for name in ("arms", "arms-all-flags", "arms-strict"))


def reviewed_flag(label: str, reviewed=None, review=None) -> str | None:
    """The flag the session audit writes for the exec script that the reviewer read for `label` (it
    quotes the script's first 200 characters), or None if the record holds no reviewed script for it."""
    entry = (REVIEWED if reviewed is None else reviewed).get(label)
    if entry is None:
        return None
    log, line, script = entry
    code = ((REVIEW if review is None else review) / script).read_text()
    return f"{log} line {line}: {EXEC_FLAG}: {code[:200]!r}"


def admit(rec, label: str, admitted: set[str]):
    """The session audit `rec` (cli._session_audit's result) as the deviation reads it: passed, if
    `label` is admitted and the audit failed on exec-script flags alone; otherwise `rec` unchanged.
    An admitted label whose audit failed for any other reason raises SystemExit."""
    if label not in admitted or not isinstance(rec, dict) or rec.get("passed"):
        return rec
    flags = rec.get("flags") or []
    threads = rec.get("threads") or []
    expected = reviewed_flag(label)
    why = [reason for reason, bad in (
        ("no flag", not flags),
        ("a flag that is not an exec script's", any(EXEC_FLAG not in str(f) for f in flags)),
        ("a script the reviewer did not read", bool(flags) and any(str(f) != expected for f in flags)),
        ("a missing thread", bool(rec.get("missing"))),
        ("an orphaned thread", bool(rec.get("orphans"))),
        ("a violation", bool(rec.get("violations"))),
        ("no thread", not threads),
        ("a thread whose own audit failed", any(not (t.get("audit") or {}).get("passed") for t in threads)),
    ) if bad]
    if why:
        raise SystemExit(f"{label}: its session audit failed on {', '.join(why)}, not on exec scripts alone")
    return {**rec, "passed": True}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--admit", action="append", default=[], metavar="LABEL",
                    help="a Sol run whose session audit failed only on exec scripts the reviewer cleared")
    ap.add_argument("--out", type=Path, required=True,
                    help="the folder for this analysis's outputs; never a registered analysis's folder")
    args, rest = ap.parse_known_args(argv)
    if not args.admit:
        raise SystemExit("name each run the reviewer cleared with --admit LABEL")
    if any(args.out.resolve() == folder.resolve() for folder in REGISTERED_OUT):
        raise SystemExit(f"--out {args.out} holds a registered analysis; give the deviation its own folder")
    admitted = set(args.admit)
    registered = cli._session_audit

    def session_audit(run_dir: Path):
        return admit(registered(run_dir), run_dir.name, admitted)

    cli._session_audit = session_audit  # accepted_runs looks the name up at each call
    try:
        rc = arms_registered.main([*rest, "--out", str(args.out)])
    finally:
        cli._session_audit = registered
    summary = args.out / "summary.md"
    lines = summary.read_text().split("\n")
    note = ("Deviation 1 (prereg/README.md), not the registered analysis: it also pools "
            + ", ".join(sorted(admitted)) + ", whose session audits failed only on exec scripts a blinded "
            "reviewer cleared (outputs/flag-review/). Every other choice is the registered analysis's.")
    summary.write_text("\n".join([lines[0], "", note, *lines[1:]]))
    return rc


if __name__ == "__main__":
    sys.exit(main())
