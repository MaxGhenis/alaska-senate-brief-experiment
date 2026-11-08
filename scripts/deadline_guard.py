"""Enforce the study's deadline on its Subfleet runs.

- From START_BY, cancel each run still waiting for a lane: at 70-110 minutes a run, it could not
  finish before KILL_AT.
- From KILL_AT, kill each run still going: every answer must be in before the Alaska Division of
  Elections' 7-day count on 2026-11-10, its first new results since election week.

Each run cancelled or killed is then marked abandoned (`i86 abandon --why`, with the reason), so
`i86 collect` can proceed. A run with no Subfleet job that is not marked finished (a dispatch cut off
before Subfleet answered) is marked abandoned at KILL_AT too.
The guard checks every 60 seconds and exits once every run in design/assignment.json has finished or
been abandoned, printing each run's state, so a session waiting on it wakes up. Until KILL_AT, a label
not yet dispatched counts as still to come, and so does one whose run folder has no meta.json yet (a
dispatch starting), so the guard may start before the first dispatch
(launchd/com.maxghenis.aksen-deadline.plist). A run that `i86 collect` has recorded as finished is
left alone, and so is a run whose state Subfleet does not give this minute (it fails, answers with
something other than JSON, or takes more than two minutes): the guard asks again the next. A pass
that fails for any other reason (a meta.json it cannot read, say) is logged, and the guard tries
again a minute later; with `--once`, it exits 1.

    .venv/bin/python scripts/deadline_guard.py [--start-by 2026-11-10T09:00:00Z] [--kill-at 2026-11-10T12:00:00Z]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
I86 = ROOT / ".venv" / "bin" / "i86"
ASSIGNMENT = ROOT / "design" / "assignment.json"
# As src/i86/runners.py: the states after which a run produces nothing more.
FINISHED = {"succeeded", "failed", "cancelled", "canceled", "lost", "killed", "expired", "abandoned"}
NOT_STARTED = {"queued", "waiting"}


def utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_state(job_id: str) -> str | None:
    """The job's state as Subfleet gives it, or None when Subfleet fails, gives no JSON, or takes too long."""
    try:
        proc = subprocess.run(["subfleet", "runs", "show", job_id, "--json"], capture_output=True, text=True,
                              timeout=120)
        if proc.returncode != 0:
            return None
        return (json.loads(proc.stdout).get("job") or {}).get("state")
    except (subprocess.TimeoutExpired, ValueError, AttributeError):
        return None


def assigned() -> list[str]:
    """Every run label in design/assignment.json."""
    return [row["label"] for row in json.loads(ASSIGNMENT.read_text())["runs"]]


def runs() -> dict[str, dict]:
    out = {}
    for d in sorted(RUNS.iterdir()) if RUNS.exists() else []:
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("backend") == "subfleet":
            out[d.name] = meta
    return out


def abandon(label: str, why: str) -> None:
    ab = subprocess.run([str(I86), "abandon", label, "--why", why], capture_output=True, text=True, cwd=ROOT,
                        timeout=120)
    print(f"{stamp()} {label}: {ab.stdout.strip()} {ab.stderr.strip()[-300:]}", flush=True)


def stop(label: str, job_id: str, reason: str) -> None:
    try:
        kill = subprocess.run(["subfleet", "kill", job_id, "--reason", reason, "--wait", "--timeout", "180"],
                              capture_output=True, text=True, timeout=240)
        print(f"{stamp()} {label}: subfleet kill rc {kill.returncode} {kill.stdout.strip()[-300:]} "
              f"{kill.stderr.strip()[-300:]}", flush=True)
    except subprocess.TimeoutExpired:
        print(f"{stamp()} {label}: subfleet kill took more than 240 s", flush=True)
    abandon(label, reason)


def guard_pass(start_by: datetime, kill_at: datetime, args) -> dict[str, str]:
    """One pass: stop what the deadline says to stop, and return every assigned run's state."""
    now = datetime.now(timezone.utc)
    current = runs()
    # Until KILL_AT, a label with no run yet may still be dispatched, and one whose folder has no
    # meta.json yet is being dispatched.
    states = {}
    if now < kill_at:
        for label in assigned():
            if label not in current and not (RUNS / label).exists():
                states[label] = "not dispatched"
            elif label not in current and not (RUNS / label / "meta.json").exists():
                states[label] = "dispatching"
    for label, meta in current.items():
        if meta.get("state") == "abandoned" or (RUNS / label / "response.md").exists():
            states[label] = meta.get("state") or "collected"
            continue
        if not meta.get("job_id"):
            state = meta.get("state") or "no job"
            states[label] = state
            if state not in FINISHED and now >= kill_at:
                abandon(label, f"no Subfleet job at {args.kill_at}")
                states[label] = "abandoned"
            continue
        if meta.get("state") in FINISHED:  # `i86 collect` recorded the job as finished
            states[label] = meta["state"]
            continue
        state = job_state(meta["job_id"])
        if state is None:  # Subfleet did not answer: ask again next minute, and stop nothing on it
            states[label] = "unknown"
            continue
        states[label] = state
        if state in FINISHED:
            continue
        if now >= kill_at:
            stop(label, meta["job_id"], f"still {state} at {args.kill_at}; the Division counts on 2026-11-10")
            states[label] = "abandoned"
        elif now >= start_by and state in NOT_STARTED:
            stop(label, meta["job_id"], f"not started by {args.start_by}; cannot finish by {args.kill_at}")
            states[label] = "abandoned"
    return states


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start-by", default="2026-11-10T09:00:00Z")
    ap.add_argument("--kill-at", default="2026-11-10T12:00:00Z")
    ap.add_argument("--once", action="store_true", help="one pass, then print every run's state and exit")
    args = ap.parse_args(argv)
    start_by, kill_at = utc(args.start_by), utc(args.kill_at)
    print(f"{stamp()} guarding {', '.join(assigned())}: cancel unstarted at {args.start_by}, kill at "
          f"{args.kill_at}", flush=True)
    while True:
        try:
            states = guard_pass(start_by, kill_at, args)
        except Exception as exc:  # a pass that fails ends nothing but that pass
            print(f"{stamp()} this pass failed: {exc!r}", flush=True)
            if args.once:
                return 1
            time.sleep(60)
            continue
        if args.once:
            print(f"{stamp()} " + ", ".join(f"{k}={v}" for k, v in states.items()), flush=True)
            return 0
        if all(s in FINISHED or s == "collected" for s in states.values()):
            print(f"{stamp()} every run is finished or abandoned: "
                  + ", ".join(f"{k}={v}" for k, v in states.items()), flush=True)
            return 0
        time.sleep(60)




if __name__ == "__main__":
    sys.exit(main())
