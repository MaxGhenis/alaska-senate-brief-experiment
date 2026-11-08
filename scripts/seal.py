"""Collect, check, audit and stamp every run's answer, then commit and push runs/ and prereg/.

The answers must be stamped before the Alaska Division of Elections' 7-day count on 2026-11-10, its
first new results since election week, so this runs two ways:

- `--now`, by hand, once every run has finished or been abandoned;
- as a launchd fallback (launchd/com.maxghenis.aksen-seal.plist), waiting until ACT_AFTER (12:00Z on
  November 8) and then sealing as soon as every run in design/assignment.json has finished or been
  abandoned, unless someone has already sealed. A label not yet dispatched counts as still going until
  KILL_AT (12:00Z on November 10), when it can no longer finish in time. The fallback gives up at
  GIVE_UP (16:00Z on November 10, 7 a.m. in Juneau), sealed or not: after that, a stamp might not
  predate the count.

Whoever seals first takes MARKER, so the two never run together; a marker older than 15 minutes
without a sealed folder counts as abandoned. Sealing first runs `i86 recover` on every run whose
Subfleet job was lost, whatever its arm, then `i86 collect`, `check`, `audit`,
`scripts/audit_codex_sessions.py` and `prereg`. It keeps the manifest and its proof in
prereg/collected/, adds that folder to prereg/README.md, and commits and pushes. The record counts as
sealed once that folder is committed and pushed; until then, the fallback tries again every minute,
and a pass that fails, at any step, ends nothing but that pass. A pass also stops, to try again, when
Subfleet does not say whether a run was lost, when `i86 recover` or `i86 collect` hits an error that a
later try may not (a succeeded run whose answer Subfleet did not hand over among them), or when
`prereg` writes no fresh manifest or its stamp fails. From LAST_CALL (30 minutes before GIVE_UP; by
hand, `--last-call`), it stops only when it has no fresh manifest: it seals what there is, without a
missing answer, or with hashes alone when the stamp fails, and GAPS.txt and the commit message name
each such gap and the runs it leaves out. A run still going at LAST_CALL gets one pass of
scripts/deadline_guard.py, which kills and abandons it; a run whose state Subfleet still does not give
is abandoned (`i86 abandon --why`) and named in GAPS.txt. Every command a pass runs has a time limit,
and no pass starts the stamp at or after GIVE_UP, or gives it longer than the time left.

    .venv/bin/python scripts/seal.py --now [--last-call] [--dry-run]
    .venv/bin/python scripts/seal.py [--act-after 2026-11-08T12:00:00Z] [--give-up 2026-11-10T16:00:00Z] [--once]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
PREREG = ROOT / "prereg"
FOLDER = PREREG / "collected"
I86 = ROOT / ".venv" / "bin" / "i86"
PYTHON = ROOT / ".venv" / "bin" / "python"
CODEX_AUDIT = ROOT / "scripts" / "audit_codex_sessions.py"
GUARD = ROOT / "scripts" / "deadline_guard.py"
ASSIGNMENT = ROOT / "design" / "assignment.json"
KILL_AT = "2026-11-10T12:00:00Z"  # as DEADLINE in src/i86/cli.py and --kill-at in scripts/deadline_guard.py
LAST_CALL = timedelta(minutes=30)  # before GIVE_UP: seal what there is
MARKER = Path.home() / ".cache" / "aksen-sealing"
STALE_S = 900
FINISHED = {"succeeded", "failed", "cancelled", "canceled", "lost", "killed", "expired", "abandoned"}
# Time limits, in seconds, for the commands a pass runs. Every write they make is whole or not at all
# (src/i86/runners.py write_atomic), so a command cut off leaves nothing a later pass cannot redo.
LIMIT = {"subfleet": 120, "abandon": 60, "recover": 300, "collect": 600, "check": 600, "audit": 900, "guard": 600,
         "git": 120, "push": 300, "prereg": 900}
UNKNOWN_AT_LAST_CALL = "Subfleet gave no state for it at the last call"


def utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rel(path: Path):
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def log(text: str) -> None:
    print(f"{stamp()} {text}", flush=True)


def job_state(job_id: str) -> str | None:
    """The job's state as Subfleet gives it, or None when Subfleet fails, gives no JSON, or takes too long."""
    try:
        proc = subprocess.run(["subfleet", "runs", "show", job_id, "--json"], capture_output=True, text=True,
                              timeout=LIMIT["subfleet"])
        if proc.returncode != 0:
            return None
        return (json.loads(proc.stdout).get("job") or {}).get("state")
    except (subprocess.TimeoutExpired, ValueError, AttributeError):
        return None


def read_meta(d: Path) -> dict | None:
    """A run folder's meta.json, or None while it has none."""
    path = d / "meta.json"
    return json.loads(path.read_text()) if path.exists() else None


def assigned() -> list[str]:
    """Every run label in design/assignment.json."""
    return [row["label"] for row in json.loads(ASSIGNMENT.read_text())["runs"]]


def pending(now: datetime | None = None) -> dict[str, str]:
    """Runs that may still produce an answer, with their states (as `i86 collect` decides), and, before
    KILL_AT, each assigned label not yet dispatched, or whose folder has no meta.json yet (a dispatch
    starting). A run whose state Subfleet does not give counts, as "unknown"."""
    now = now or datetime.now(timezone.utc)
    out = {}
    have = {d.name for d in RUNS.iterdir()} if RUNS.exists() else set()
    if now < utc(KILL_AT):
        out.update({label: "not dispatched" for label in assigned() if label not in have})
        out.update({label: "dispatching" for label in assigned()
                    if label in have and not (RUNS / label / "meta.json").exists()})
    for d in sorted(RUNS.iterdir()) if RUNS.exists() else []:
        meta = read_meta(d)
        if meta is None or (d / "response.md").exists():
            continue
        if meta.get("state") in FINISHED:
            continue
        state = job_state(meta["job_id"]) if meta.get("backend") == "subfleet" and meta.get("job_id") else None
        if state not in FINISHED:
            out[d.name] = state or "unknown"
    return out


def lost() -> tuple[list[str], list[str]]:
    """Runs whose Subfleet job was lost, neither collected, abandoned nor recovered: `i86 recover` may
    take their answers from their own transcripts. Sealing tries every one, whatever its arm. Also the
    runs Subfleet did not say about, which may be lost too."""
    out, unknown = [], []
    for d in sorted(RUNS.iterdir()) if RUNS.exists() else []:
        meta = read_meta(d)
        if meta is None or (d / "response.md").exists():
            continue
        if (meta.get("backend") != "subfleet" or not meta.get("job_id") or meta.get("state") == "abandoned"
                or meta.get("recovered")):
            continue
        state = job_state(meta["job_id"])
        if state == "lost":
            out.append(d.name)
        elif state is None:
            unknown.append(d.name)
    return out, unknown


def unfetched() -> list[str]:
    """Runs that succeeded but have no answer in runs/: `i86 collect` could not copy it."""
    out = []
    for d in sorted(RUNS.iterdir()) if RUNS.exists() else []:
        meta = read_meta(d)
        if meta is not None and meta.get("state") == "succeeded" and not (d / "response.md").exists():
            out.append(d.name)
    return out


def git(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=LIMIT["git"])
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["git", *args], 124, "", f"timed out after {LIMIT['git']} s")


def sealed() -> bool:
    """Whether prereg/collected/ exists, with runs/ and prereg/ committed and pushed."""
    if not FOLDER.exists():
        return False
    dirty, ahead = git("status", "--porcelain", "--", "runs", "prereg"), git("rev-list", "--count", "@{u}..HEAD")
    return (dirty.returncode == 0 and not dirty.stdout.strip() and ahead.returncode == 0
            and ahead.stdout.strip() == "0")


def claim(who: str) -> bool:
    """Take the sealing marker, or say someone else holds it."""
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


def run(cmd: list, timeout: float = 600) -> subprocess.CompletedProcess:
    """Run a command in its own process group. Past `timeout` seconds the whole group is killed, and
    the result has return code 124."""
    argv = [str(c) for c in cmd]
    child = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             start_new_session=True)
    try:
        out, err = child.communicate(timeout=max(1.0, timeout))
        rc = child.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
        for pipe in (child.stdout, child.stderr):  # a process outside the group may hold them open
            pipe.close()
        out, err, rc = "", f"timed out after {timeout:.0f} s; killed", 124
    proc = subprocess.CompletedProcess(argv, rc, out or "", err or "")
    log(f"$ {' '.join(argv)} -> rc {proc.returncode}")
    for text in (proc.stdout.strip(), proc.stderr.strip()):
        if text:
            print(text[-4000:], flush=True)
    return proc


def seal(who: str, last_call: bool = False, give_up: datetime | None = None) -> int:
    """Collect, check, audit and stamp every answer into prereg/collected/ unless that folder exists,
    then commit and push runs/ and prereg/. Returns 0 once everything is pushed. Before the last call, a
    step that a later try may mend stops the pass (returns 1); at the last call, only a missing manifest
    does, and GAPS.txt and the commit message name what is missing. A run whose state Subfleet does not
    give at the last call is abandoned and named. With `give_up`, no command before the commit runs past
    it, and the stamp never starts at or after it; the commit and push may end later, since the stamp
    they carry was made in time."""
    gaps = []

    def left(limit: float) -> float:
        """A command's time limit: its own, cut to the time left before `give_up`."""
        if give_up is None:
            return limit
        return min(limit, (give_up - datetime.now(timezone.utc)).total_seconds())

    def mendable(step: str, proc: subprocess.CompletedProcess) -> bool:
        """Whether `step` timed out before the last call, which stops the pass; at the last call, a gap."""
        if proc.returncode != 124:
            return False
        if not last_call:
            log(f"{step} timed out; not sealing")
            return True
        gaps.append(f"{step} timed out, so its record is incomplete")
        return False

    if not FOLDER.exists():
        lost_runs, unknown = lost()
        if unknown:
            if not last_call:
                log(f"Subfleet did not say whether {', '.join(unknown)} were lost; not sealing")
                return 1
            for label in unknown:
                run([I86, "abandon", label, "--why", UNKNOWN_AT_LAST_CALL], timeout=left(LIMIT["abandon"]))
            gaps.append(f"Subfleet gave no state at the last call for {', '.join(unknown)}: abandoned, "
                        "with no answer collected")
        for label in lost_runs:
            rec = run([I86, "recover", label], timeout=left(LIMIT["recover"]))  # changes nothing unless it recovers
            if rec.returncode not in (0, 2):  # 2: nothing to recover; anything else may pass
                if not last_call:
                    log(f"recovering {label} failed; not sealing")
                    return 1
                gaps.append(f"recovering {label} failed, so its answer is not collected")
        collect = run([I86, "collect"], timeout=left(LIMIT["collect"]))
        if "still going" in collect.stdout:
            log("a run is still going: collect copied no answers; not sealing")
            return 1
        if collect.returncode != 0:
            if not last_call:
                log("collect left a succeeded run's answer out; not sealing")
                return 1
            missing = unfetched()
            gaps.append(f"collect could not copy the answers of {', '.join(missing)} (see each run's collect_error)"
                        if missing else f"collect exited {collect.returncode} (see the log)")
        # check exits 1 when an answer fails check, audit when an audit fails, and the session audit
        # (every thread behind each Sol run) likewise: those runs stay out of the pool.
        for step, cmd, limit in (("i86 check", [I86, "check"], LIMIT["check"]),
                                 ("i86 audit", [I86, "audit"], LIMIT["audit"]),
                                 ("the session audit", [PYTHON, CODEX_AUDIT], LIMIT["audit"])):
            if mendable(step, run(cmd, timeout=left(limit))):
                return 1
        if give_up is not None and datetime.now(timezone.utc) >= give_up:
            log("past the give-up time: a stamp now would not predate the results; not stamping")
            return 1
        sums, proof = PREREG / "SHA256SUMS", PREREG / "SHA256SUMS.ots"
        started = time.time()
        prereg = run([I86, "prereg"], timeout=left(LIMIT["prereg"]))
        # Fresh: written by this prereg (passes run a minute apart), listing every answer collected.
        listed = ({line.split("  ", 1)[1] for line in sums.read_text().splitlines() if "  " in line}
                  if sums.exists() and sums.stat().st_mtime >= started - 1 else None)
        if listed is None or any(str(p.relative_to(ROOT)) not in listed for p in RUNS.glob("*/response.md")):
            log("prereg wrote no fresh manifest of every answer; not sealing")
            return 1
        has_proof = prereg.returncode == 0 and proof.exists()
        if not has_proof:
            if not last_call:
                log("the OpenTimestamps stamp failed; not sealing yet")
                return 1
            if give_up is not None and datetime.now(timezone.utc) >= give_up:
                log("the stamp failed and the give-up time has passed; not sealing")
                return 1
            gaps.append("the OpenTimestamps stamp failed: hashes only")
        staging = FOLDER.with_name(FOLDER.name + ".partial")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        shutil.copy2(sums, staging / sums.name)
        if has_proof:
            shutil.copy2(proof, staging / proof.name)
        if gaps:
            (staging / "GAPS.txt").write_text("".join(f"{g}\n" for g in gaps))
        staging.rename(FOLDER)  # the folder appears whole or not at all
        when = datetime.now(timezone.utc).strftime("%H:%MZ %m/%d")
        readme = PREREG / "README.md"
        lines = readme.read_text().splitlines()
        if not any(line.startswith(f"| `{FOLDER.name}/`") for line in lines):
            rows = [i for i, line in enumerate(lines) if line.startswith("| `") and line.split("`")[1].endswith("/")]
            lines.insert(rows[-1] + 1 if rows else len(lines),
                         f"| `{FOLDER.name}/` | {when} | every run's answer, parsed response, repairs, audit and "
                         f"transcript, collected once the last run had finished |")
            readme.write_text("\n".join(lines) + "\n")
    stamped = ("stamped with OpenTimestamps" if (FOLDER / "SHA256SUMS.ots").exists()
               else "OpenTimestamps stamp FAILED; hashes only")
    gaps_file = FOLDER / "GAPS.txt"
    noted = (f"Sealed at the last call, with gaps:\n{gaps_file.read_text()}\n" if gaps_file.exists() else "")
    message = (f"Collect every run's answer and transcript, check, audit and stamp them ({stamped})\n\n"
               f"Sealed by {who} at {stamp()}, before the Division of Elections' count on 2026-11-10.\n\n"
               f"{noted}"
               "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    if run(["git", "add", "runs", "prereg"], timeout=LIMIT["git"]).returncode != 0:
        return 1
    if git("diff", "--cached", "--quiet", "--", "runs", "prereg").returncode != 0:
        if run(["git", "commit", "-m", message, "--", "runs", "prereg"], timeout=LIMIT["git"]).returncode != 0:
            return 1
    push = run(["git", "push", "origin", "HEAD"], timeout=LIMIT["push"])
    run(["git", "log", "-1", "--format=%h %cI%n%B"], timeout=LIMIT["git"])
    return 0 if push.returncode == 0 and sealed() else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--now", action="store_true", help="seal now, by hand; fails if any run is still going")
    ap.add_argument("--last-call", action="store_true",
                    help="with --now: seal what there is, without a missing answer or stamp")
    ap.add_argument("--act-after", default="2026-11-08T12:00:00Z")
    ap.add_argument("--give-up", default="2026-11-10T16:00:00Z")
    ap.add_argument("--once", action="store_true", help="one pass of the fallback's checks, then exit")
    ap.add_argument("--dry-run", action="store_true", help="report what would happen; seal nothing")
    args = ap.parse_args(argv)
    who = "hand (scripts/seal.py --now)" if args.now else "the launchd fallback (scripts/seal.py)"
    act_after, give_up = utc(args.act_after), utc(args.give_up)
    if not args.now:
        log(f"fallback armed: seals after {args.act_after} once every assigned run is done, unless already "
            f"sealed; seals what there is from {(give_up - LAST_CALL).strftime('%Y-%m-%dT%H:%M:%SZ')}; "
            f"gives up at {args.give_up}")
    while True:
        now = datetime.now(timezone.utc)
        last_call = args.last_call if args.now else now >= give_up - LAST_CALL
        try:
            if sealed():
                log(f"{rel(FOLDER)} exists, committed and pushed: already sealed")
                return 0
            if not args.now and now >= give_up:
                log("past the give-up time: a stamp now would not predate the results; not sealing"
                    if not FOLDER.exists() else
                    f"past the give-up time with {rel(FOLDER)} not pushed: stopping; push it by hand")
                return 1
            if args.now or now >= act_after:
                waiting = {} if FOLDER.exists() else pending(now)
                if waiting and last_call:
                    # The guard should have ended them at KILL_AT: one pass of it now. A run whose state
                    # Subfleet still does not give holds nothing up: seal() abandons it and names it.
                    log("still going at the last call: " + ", ".join(f"{k}={v}" for k, v in waiting.items()))
                    run([PYTHON, GUARD, "--once"], timeout=min(LIMIT["guard"], (give_up - now).total_seconds()))
                    waiting = {k: v for k, v in pending().items() if v != "unknown"}
                if waiting:
                    log("still going: " + ", ".join(f"{k}={v}" for k, v in waiting.items()))
                    if args.now:
                        return 1
                elif args.dry_run:
                    log("every run is done; would claim the marker and seal" if not FOLDER.exists()
                        else f"{rel(FOLDER)} exists but is not pushed; would commit and push it")
                    return 0
                elif claim(who):
                    try:
                        rc = seal(who, last_call, give_up)
                    finally:
                        MARKER.unlink(missing_ok=True)
                    if rc == 0 or args.now:
                        return rc
                    log("sealing did not finish; trying again in 60 s")
                else:
                    log(f"someone else holds {MARKER} ({MARKER.read_text().strip() if MARKER.exists() else 'gone'})")
                    if args.now:
                        return 1
        except Exception as exc:  # subfleet, git or the network can fail for a minute; the fallback goes on
            log(f"this pass failed: {exc!r}")
            if args.now:
                return 1
        if args.once:
            return 0
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
