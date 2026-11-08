"""Enforce the study's deadline on its Subfleet runs.

- From START_BY, cancel each run still waiting for a lane: at 70-110 minutes a run, it could not
  finish before KILL_AT.
- From KILL_AT, kill each run still going: every answer must be in before the Alaska Division of
  Elections' 7-day count on 2026-11-10, its first new results since election week.

Each run cancelled or killed is then marked abandoned (`i86 abandon`), so `i86 collect` can proceed.
The guard checks every 60 seconds and exits once every run has finished or been abandoned, printing
each run's state, so a session waiting on it wakes up.

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
FINISHED = {"succeeded", "failed", "cancelled", "lost"}
NOT_STARTED = {"queued", "waiting"}


def utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_state(job_id: str) -> str | None:
    proc = subprocess.run(["subfleet", "runs", "show", job_id, "--json"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return (json.loads(proc.stdout).get("job") or {}).get("state")


def runs() -> dict[str, dict]:
    out = {}
    for d in sorted(RUNS.iterdir()) if RUNS.exists() else []:
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("backend") == "subfleet" and meta.get("job_id"):
            out[d.name] = meta
    return out


def stop(label: str, job_id: str, reason: str) -> None:
    kill = subprocess.run(["subfleet", "kill", job_id, "--reason", reason, "--wait", "--timeout", "180"],
                          capture_output=True, text=True)
    print(f"{stamp()} {label}: subfleet kill rc {kill.returncode} {kill.stdout.strip()[-300:]} {kill.stderr.strip()[-300:]}",
          flush=True)
    ab = subprocess.run([str(I86), "abandon", label], capture_output=True, text=True, cwd=ROOT)
    print(f"{stamp()} {label}: {ab.stdout.strip()} {ab.stderr.strip()[-300:]}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start-by", default="2026-11-10T09:00:00Z")
    ap.add_argument("--kill-at", default="2026-11-10T12:00:00Z")
    ap.add_argument("--once", action="store_true", help="one pass, then print every run's state and exit")
    args = ap.parse_args(argv)
    start_by, kill_at = utc(args.start_by), utc(args.kill_at)
    print(f"{stamp()} guarding {', '.join(runs())}: cancel unstarted at {args.start_by}, kill at {args.kill_at}",
          flush=True)
    while True:
        now = datetime.now(timezone.utc)
        states = {}
        for label, meta in runs().items():
            if meta.get("state") == "abandoned" or (RUNS / label / "response.md").exists():
                states[label] = meta.get("state") or "collected"
                continue
            state = job_state(meta["job_id"])
            states[label] = state
            if state in FINISHED:
                continue
            if now >= kill_at:
                stop(label, meta["job_id"], f"still {state} at {args.kill_at}; the Division counts on 2026-11-10")
                states[label] = "abandoned"
            elif now >= start_by and state in NOT_STARTED:
                stop(label, meta["job_id"], f"not started by {args.start_by}; cannot finish by {args.kill_at}")
                states[label] = "abandoned"
        if args.once:
            print(f"{stamp()} " + ", ".join(f"{k}={v}" for k, v in states.items()), flush=True)
            return 0
        if all(s in FINISHED or s == "abandoned" for s in states.values()):
            print(f"{stamp()} every run is finished or abandoned: "
                  + ", ".join(f"{k}={v}" for k, v in states.items()), flush=True)
            return 0
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
