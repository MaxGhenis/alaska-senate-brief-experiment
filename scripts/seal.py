"""Collect, check, audit and stamp every run's answer, then commit and push runs/ and prereg/.

The answers must be stamped before the Alaska Division of Elections' 7-day count on 2026-11-10, its
first new results since election week, so this runs two ways:

- `--now`, by hand, once every run has finished or been abandoned;
- as a launchd fallback, waiting until ACT_AFTER (12:00Z on November 8) and then sealing as soon as
  every run has finished or been abandoned, unless someone has already sealed. It gives up at
  GIVE_UP (16:00Z on November 10, 7 a.m. in Juneau): after that, a stamp might not predate the count.

Whoever seals first takes MARKER, so the two never run together; a marker older than 15 minutes
without a sealed folder counts as abandoned. Sealing runs `i86 collect`, `check`, `audit` and
`prereg`, keeps the manifest and its proof in prereg/collected/, adds that folder to
prereg/README.md, and commits and pushes.

    .venv/bin/python scripts/seal.py --now [--dry-run]
    .venv/bin/python scripts/seal.py [--act-after 2026-11-08T12:00:00Z] [--give-up 2026-11-10T16:00:00Z] [--once]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
PREREG = ROOT / "prereg"
FOLDER = PREREG / "collected"
I86 = ROOT / ".venv" / "bin" / "i86"
MARKER = Path.home() / ".cache" / "aksen-sealing"
STALE_S = 900
FINISHED = {"succeeded", "failed", "cancelled", "canceled", "lost", "killed", "expired", "abandoned"}


def utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(text: str) -> None:
    print(f"{stamp()} {text}", flush=True)


def job_state(job_id: str) -> str | None:
    proc = subprocess.run(["subfleet", "runs", "show", job_id, "--json"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return (json.loads(proc.stdout).get("job") or {}).get("state")


def pending() -> dict[str, str]:
    """Runs that may still produce an answer, with their states (as `i86 collect` decides)."""
    out = {}
    for d in sorted(RUNS.iterdir()) if RUNS.exists() else []:
        meta_path = d / "meta.json"
        if not meta_path.exists() or (d / "response.md").exists():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("state") in FINISHED:
            continue
        state = job_state(meta["job_id"]) if meta.get("backend") == "subfleet" and meta.get("job_id") else None
        if state not in FINISHED:
            out[d.name] = state or "unknown"
    return out


def claim(who: str) -> bool:
    """Take the sealing marker, or say someone else holds it or has already sealed."""
    if FOLDER.exists():
        return False
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    if MARKER.exists() and time.time() - MARKER.stat().st_mtime > STALE_S:
        log(f"marker older than {STALE_S} s and nothing sealed: taking it over ({MARKER.read_text().strip()})")
        MARKER.unlink()
    try:
        fd = os.open(MARKER, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(f"{who} pid {os.getpid()} at {stamp()}\n")
    return True


def run(cmd: list) -> subprocess.CompletedProcess:
    proc = subprocess.run([str(c) for c in cmd], cwd=ROOT, capture_output=True, text=True)
    log(f"$ {' '.join(str(c) for c in cmd)} -> rc {proc.returncode}")
    for text in (proc.stdout.strip(), proc.stderr.strip()):
        if text:
            print(text[-4000:], flush=True)
    return proc


def seal(who: str) -> int:
    collect = run([I86, "collect"])
    if collect.returncode != 0 or "still going" in collect.stdout:
        log("collect copied no answers; not sealing")
        return 1
    run([I86, "check"])  # exit 1 when an answer fails check: those stay out of the pool
    run([I86, "audit"])  # exit 1 when an audit fails: likewise
    prereg = run([I86, "prereg"])
    FOLDER.mkdir(parents=True)
    for name in ("SHA256SUMS", "SHA256SUMS.ots"):
        if (PREREG / name).exists():
            shutil.copy2(PREREG / name, FOLDER / name)
    when = datetime.now(timezone.utc).strftime("%H:%MZ %m/%d")
    readme = PREREG / "README.md"
    lines = readme.read_text().splitlines()
    rows = [i for i, line in enumerate(lines) if line.startswith("| `") and line.split("`")[1].endswith("/")]
    lines.insert(rows[-1] + 1 if rows else len(lines),
                 f"| `{FOLDER.name}/` | {when} | every run's answer, parsed response, audit and "
                 f"transcript, collected once the last run had finished |")
    readme.write_text("\n".join(lines) + "\n")
    stamped = "stamped with OpenTimestamps" if prereg.returncode == 0 else "OpenTimestamps stamp FAILED; hashes only"
    message = (f"Collect every run's answer and transcript, check, audit and stamp them ({stamped})\n\n"
               f"Sealed by {who} at {stamp()}, before the Division of Elections' count on 2026-11-10.\n\n"
               "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    run(["git", "add", "runs", "prereg"])
    commit = run(["git", "commit", "-m", message, "--", "runs", "prereg"])
    push = run(["git", "push", "origin", "HEAD"])
    run(["git", "log", "-1", "--format=%h %cI%n%B"])
    return 0 if commit.returncode == 0 and push.returncode == 0 and prereg.returncode == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--now", action="store_true", help="seal now, by hand; fails if any run is still going")
    ap.add_argument("--act-after", default="2026-11-08T12:00:00Z")
    ap.add_argument("--give-up", default="2026-11-10T16:00:00Z")
    ap.add_argument("--once", action="store_true", help="one pass of the fallback's checks, then exit")
    ap.add_argument("--dry-run", action="store_true", help="report what would happen; seal nothing")
    args = ap.parse_args(argv)
    who = "hand (scripts/seal.py --now)" if args.now else "the launchd fallback (scripts/seal.py)"
    act_after, give_up = utc(args.act_after), utc(args.give_up)
    if not args.now:
        log(f"fallback armed: seals after {args.act_after} once every run is done, unless already sealed; "
            f"gives up at {args.give_up}")
    while True:
        now = datetime.now(timezone.utc)
        if FOLDER.exists():
            log(f"{FOLDER.relative_to(ROOT)} exists: already sealed")
            return 0
        if not args.now and now >= give_up:
            log("past the give-up time: a stamp now would not predate the results; not sealing")
            return 1
        if args.now or now >= act_after:
            waiting = pending()
            if waiting:
                log("still going: " + ", ".join(f"{k}={v}" for k, v in waiting.items()))
                if args.now:
                    return 1
            elif args.dry_run:
                log("every run is done; would claim the marker and seal")
                return 0
            elif claim(who):
                try:
                    return seal(who)
                finally:
                    MARKER.unlink(missing_ok=True)
            else:
                log(f"someone else holds {MARKER} ({MARKER.read_text().strip() if MARKER.exists() else 'gone'})")
                if args.now:
                    return 1
        if args.once:
            return 0
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
