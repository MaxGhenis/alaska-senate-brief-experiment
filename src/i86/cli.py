"""Command line: i86 prompt | assign | dispatch | collect | abandon | recover | check | audit | prereg.

    i86 prompt                      write the four arms' briefs (prompts/arm-*.md, prompts/SHA256SUMS)
                                    and schemas/template.json
    i86 assign                      write design/assignment.json: each run's family, arm and order
    i86 dispatch subfleet LABEL --tier standard -m opus      (or --tier hard, no -m, for GPT-6.1 Sol)
                                    sends the run its arm's brief, from design/assignment.json
    i86 collect                     refresh subfleet runs; fetch answers once all have finished
    i86 abandon LABEL               mark a run that will never finish (final)
    i86 recover LABEL               take a lost subfleet run's final message from its own transcript
    i86 check                       parse every runs/*/response.md into runs/*/response.json
    i86 audit                       check each run's transcript for local file reads
    i86 simulate                    (the DC and Texas maps only: it refuses this map's range cells;
                                    scripts/arms.py analyses this study)
    i86 prereg                      hash the prompt, graph, runs and outputs into prereg/SHA256SUMS
                                    and request an OpenTimestamps proof
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import audit, graph as graph_mod, prompt, report, runners
from .graph import ROOT
from .response import ResponseError, parse, read_answer, template
from .summarize import pool

PROMPT_DIR = ROOT / "prompts"
ASSIGNMENT = ROOT / "design" / "assignment.json"
# A forecaster's prior_context that mentions these questions, other than a bare "none", means it saw
# something beyond the brief. The DC and Texas studies' terms stay: a run that saw them saw this
# study's sister briefs.
DISCLOSURE = re.compile(r"peltola|sullivan|alaska|measure.?2|ranked.?choice|kreiss|placebo|discontinu|"
                        r"foie|initiative.?86|\bi-?86\b|force.?fe|hinojosa|abbott|talarico|paxton|"
                        r"data.?cent|ercot", re.I)
BARE_NONE = re.compile(r"\W*(?:none|nothing|n/?a)\W*", re.I)


def discloses(prior_context) -> bool:
    text = str(prior_context or "")
    return not BARE_NONE.fullmatch(text) and bool(DISCLOSURE.search(text))
TEMPLATE_PATH = ROOT / "schemas" / "template.json"
OUTPUTS = ROOT / "outputs"


def cmd_prompt(args) -> int:
    g = graph_mod.load()
    shas = prompt.write(g, PROMPT_DIR)
    TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATE_PATH.write_text(json.dumps(template(g), indent=1) + "\n")
    for arm, sha in shas.items():
        print(f"{prompt.path_for(PROMPT_DIR, arm).relative_to(ROOT)} sha256 {sha}")
    return 0


def _prompt_sha(arm: str | None = None):
    """The current brief's SHA-256 for one arm, or {arm: sha} for every arm, after checking that the
    committed briefs match what the map and context build."""
    g = graph_mod.load()
    shas = {}
    for a in (prompt.ARMS if arm is None else (arm,)):
        text = prompt.build(g, a)
        path = prompt.path_for(PROMPT_DIR, a)
        if not path.exists() or path.read_text() != text:
            raise SystemExit(f"{path.relative_to(ROOT)} is stale; run `i86 prompt` and commit it first")
        shas[a] = prompt.sha256(text)
    return shas if arm is None else shas[arm]


SEED = 20261108
FAMILIES = {"opus": {"tier": "standard", "model": "opus"}, "sol": {"tier": "hard", "model": None}}
# The model that must serve each family's runs, as Subfleet records it in meta.json: a run that another
# model served stays out of the analysis.
SERVED = {"opus": "claude-opus-5-5", "sol": "gpt-6.1-sol"}
PER_ARM = 3


def assignment(seed: int = SEED, per_arm: int = PER_ARM) -> dict:
    """Each run's family, arm and dispatch order. Within each family, every arm gets `per_arm` runs in an
    order a seeded shuffle sets; the families then alternate, the first chosen by the same generator."""
    import random

    rng = random.Random(seed)
    orders = {}
    for fam in FAMILIES:
        arms = [a for a in prompt.ARMS for _ in range(per_arm)]
        rng.shuffle(arms)
        orders[fam] = arms
    fams = list(FAMILIES)
    if rng.random() < 0.5:
        fams.reverse()
    runs = []
    for i in range(len(prompt.ARMS) * per_arm):
        for fam in fams:
            runs.append({"order": len(runs) + 1, "label": f"{fam}-{i + 1:02d}", "family": fam,
                         "arm": orders[fam][i], **FAMILIES[fam]})
    return {"seed": seed, "per_arm": per_arm, "arms": list(prompt.ARMS), "runs": runs}


def cmd_assign(args) -> int:
    plan = assignment(args.seed)
    if ASSIGNMENT.exists() and json.loads(ASSIGNMENT.read_text()) != plan and not args.force:
        raise SystemExit(f"{ASSIGNMENT.relative_to(ROOT)} exists and differs; the assignment is made once")
    ASSIGNMENT.parent.mkdir(parents=True, exist_ok=True)
    ASSIGNMENT.write_text(json.dumps(plan, indent=1) + "\n")
    print(f"{len(plan['runs'])} runs written to {ASSIGNMENT.relative_to(ROOT)} (seed {plan['seed']})")
    return 0


def planned(label: str) -> dict:
    """A run's row in design/assignment.json, which must match what assignment() builds."""
    if not ASSIGNMENT.exists():
        raise SystemExit("no design/assignment.json; run `i86 assign` and commit it first")
    plan = json.loads(ASSIGNMENT.read_text())
    if plan != assignment(plan.get("seed", SEED), plan.get("per_arm", PER_ARM)):
        raise SystemExit("design/assignment.json does not match its seed; never edit it by hand")
    rows = [r for r in plan["runs"] if r["label"] == label]
    if len(rows) != 1:
        raise SystemExit(f"{label} is not a run in design/assignment.json")
    return rows[0]


def cmd_dispatch(args) -> int:
    row = planned(args.label)
    if args.backend != "subfleet" or args.tier != row["tier"] or args.model != row["model"]:
        raise SystemExit(f"{args.label} is a {row['family']} run: dispatch it with subfleet --tier {row['tier']}"
                         + (f" -m {row['model']}" if row["model"] else " and no -m"))
    sha = _prompt_sha(row["arm"])
    path = prompt.path_for(PROMPT_DIR, row["arm"])
    meta = runners.dispatch_subfleet(args.label, path, sha, args.tier, args.model, args.late,
                                     arm=row["arm"], family=row["family"], order=row["order"])
    print(json.dumps({k: meta.get(k) for k in ("label", "arm", "job_id", "backend", "dispatched_at")}))
    return 0


def run_dirs() -> list[Path]:
    return runners.run_dirs()


def cmd_collect(args) -> int:
    """Refresh every subfleet run; copy answers in only once every run, of any backend, has finished.

    The whole check-and-copy holds the runs' lock, so no dispatch can start a run in between; before
    copying, the runs are listed again and must match what was checked."""
    with runners.bookkeeping_lock():
        dirs = run_dirs()
        metas = {d: (runners.refresh_subfleet(d) if runners.read_meta(d).get("backend") == "subfleet"
                     else runners.read_meta(d)) for d in dirs}
        pending = [d.name for d, m in metas.items() if runners.still_going(m)]
        unknown = {m.get("state") for m in metas.values() if m.get("backend") == "subfleet"} - runners.FINISHED - {
            "queued", "waiting", "running", None}
        if unknown:
            print(f"unknown subfleet state(s) {sorted(unknown)}: treated as still going", file=sys.stderr)
        if not pending:
            now_dirs = runners.run_dirs()
            pending = [d.name for d in now_dirs if d not in metas or runners.still_going(runners.read_meta(d))]
            for d in now_dirs:
                metas.setdefault(d, runners.read_meta(d))
        unfetched = []
        if not pending:
            for d in dirs:
                if not runners.seal(d) and runners.read_meta(d).get("state") == "succeeded":
                    unfetched.append(d.name)
    for d, meta in metas.items():
        have = (d / "response.md").exists()
        print(f"{d.name:24s} {meta.get('state') or '-':10s} {meta.get('model_served') or '-':18s} response={'yes' if have else 'no'}")
    if pending:
        print(f"{len(pending)} run(s) still going ({', '.join(pending)}); no answers copied yet. "
              "`i86 abandon <label>` marks a run that will never finish; `i86 recover <label>` takes a lost "
              "subfleet run's answer from its transcript")
    if unfetched:
        # A run that succeeded but whose answer could not be copied (Subfleet did not answer, or a
        # recovered transcript changed): collect again; `collect_error` in its meta.json says why.
        print(f"{len(unfetched)} succeeded run(s) without an answer yet ({', '.join(unfetched)}); "
              "run `i86 collect` again")
        return 1
    return 0


def cmd_abandon(args) -> int:
    meta = runners.abandon(args.label)
    print(f"{args.label}: abandoned at {meta['abandoned_at']}")
    return 0


def cmd_recover(args) -> int:
    """Exit 0 once recovered; 2 when there is nothing the run's transcript may give (the run stays out);
    any other failure, such as Subfleet not answering, exits 1, and a later attempt may succeed."""
    try:
        meta = runners.recover(args.label)
    except runners.Unrecoverable as exc:
        print(f"{args.label}: not recovered: {exc}", file=sys.stderr)
        return 2
    print(f"{args.label}: recovered from {meta['attempt_id']} (subfleet: {meta['backend_state']})")
    return 0


def cmd_check(args) -> int:
    g = graph_mod.load()
    bad = 0
    for d in run_dirs():
        src = d / "response.md"
        if not src.exists():
            continue
        try:
            obj, repairs = read_answer(g, src.read_text())
            parse(g, obj)
        except Exception as exc:  # any fault fails this answer alone; the check goes on to the next run
            problems = exc.problems if isinstance(exc, ResponseError) else [f"the answer cannot be read: {exc!r}"]
            bad += 1
            (d / "problems.txt").write_text("\n".join(problems) + "\n")
            (d / "response.json").unlink(missing_ok=True)
            (d / REPAIRS).unlink(missing_ok=True)
            print(f"{d.name}: {len(problems)} problem(s); see runs/{d.name}/problems.txt")
            continue
        (d / "response.json").write_text(json.dumps(obj, indent=1) + "\n")
        (d / "problems.txt").unlink(missing_ok=True)
        if repairs:
            (d / REPAIRS).write_text(json.dumps(repairs, indent=1) + "\n")
            print(f"{d.name}: ok after {len(repairs)} registered repair(s); see runs/{d.name}/{REPAIRS}")
        else:
            (d / REPAIRS).unlink(missing_ok=True)
            print(f"{d.name}: ok")
    return 1 if bad else 0


REPAIRS = "repairs.json"


def repaired(run_dir: Path) -> bool:
    """Whether `i86 check` made a registered repair to the run's answer (`scripts/arms.py --strict`)."""
    return (run_dir / REPAIRS).exists()


def cmd_audit(args) -> int:
    worst = 0
    for d in run_dirs():
        meta = runners.read_meta(d)
        if not (d / "response.md").exists() or meta.get("backend") == "manual":
            continue
        # Only the committed copy, which prereg hashes: a collected run without one fails, since the
        # job store's copy can change after collection.
        path = d / "transcript.jsonl"
        if not path.is_file():
            result = {"events": 0, "passed": False, "violations": [], "web_calls": 0, "local_calls": [],
                      "suspect": [], "error": f"no committed transcript at runs/{d.name}/transcript.jsonl"}
        else:
            result = audit.audit_transcript(path, meta.get("workdir", ""))
        (d / "audit.json").write_text(json.dumps(result, indent=1) + "\n")
        flag = "ok" if result["passed"] else ("VIOLATION" if result["violations"] else "NO EVENTS")
        worst = max(worst, 0 if result["passed"] else 1)
        print(f"{d.name}: {flag}; {result['events']} events, {result['web_calls']} web calls, "
              f"{len(result['suspect'])} suspect, {len(result['local_calls'])} other local calls")
        for rec in result["violations"] + result["suspect"]:
            print(f"    {rec.get('why', '')}: {rec['call'][:160]}")
    return worst


def _audit_record(result) -> bool:
    """Whether an audit.json holds what `audit_transcript` writes: whether the run passed, and its
    violations and suspect calls as lists. A record without them (damaged, cut short so that it is
    not JSON, or an older audit's) keeps its run out of the pool, as no audit does."""
    return (isinstance(result, dict) and isinstance(result.get("passed"), bool)
            and isinstance(result.get("violations"), list) and isinstance(result.get("suspect"), list))


SESSIONS = "codex-sessions"


def _session_audit(run_dir: Path):
    """A Codex run's audit of every thread, sub-agents included (`scripts/audit_codex_sessions.py`):
    the record, None if the run has none, or "unreadable" if it is not such a record."""
    path = run_dir / SESSIONS / "audit.json"
    if not path.exists():
        return None
    try:
        rec = json.loads(path.read_text())
    except (ValueError, RecursionError):
        return "unreadable"
    ok = (isinstance(rec, dict) and isinstance(rec.get("passed"), bool) and isinstance(rec.get("threads"), list)
          and all(isinstance(t, dict) and isinstance((t.get("audit") or {}).get("suspect"), list)
                  for t in rec["threads"]))
    return rec if ok else "unreadable"


def _sub_agent_suspects(session: dict) -> list:
    """The suspect calls a session audit found in sub-agents, which the transcript audit never sees."""
    return [call for t in session["threads"] if t.get("spawned_by") for call in t["audit"]["suspect"]]


def _codex_suspects(transcript: list, session: dict) -> list:
    """A Codex run's suspect calls. Its transcript and its session logs both record the main thread,
    though the two may quote a command differently, so the main thread's calls come from whichever
    audit lists more; only the session logs record the sub-agents'."""
    main = [call for t in session["threads"] if not t.get("spawned_by") for call in t["audit"]["suspect"]]
    return [*max(transcript, main, key=len), *_sub_agent_suspects(session)]


# Every answer must be in before the Division of Elections' count on 2026-11-10: a subfleet run that
# finished later (scripts/deadline_guard.py kills runs still going at this time) stays out.
DEADLINE = "2026-11-10T12:00:00Z"


def _utc(text):
    """An ISO 8601 time with a zone (a trailing Z or an offset), or None."""
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def _prompt_problem(run_dir: Path, meta: dict) -> str | None:
    """Why a subfleet run's model may not have read its arm's brief unchanged, or None.
    runs/<label>/prompt.md must hash to the brief the run records, and prompt.sent.md, what the lane
    gave the model, must hold those bytes unchanged (a Claude lane puts its own preamble first)."""
    brief, sent = run_dir / "prompt.md", run_dir / "prompt.sent.md"
    if not brief.is_file() or hashlib.sha256(brief.read_bytes()).hexdigest() != meta.get("prompt_sha256"):
        return "its prompt.md does not hash to the brief it records"
    if not sent.is_file():
        return "it has no prompt.sent.md, so what the model read is unknown"
    if brief.read_bytes() not in sent.read_bytes():
        return "its prompt.sent.md does not hold its arm's brief unchanged"
    return None


def accepted_runs(g, prompt_sha, allow_unaudited: bool = False, accept_prior: set[str] = frozenset(),
                  accept_suspect: set[str] = frozenset()) -> dict:
    """Runs with a parsed response, the current prompt's hash (`prompt_sha`: one hash, or {arm: hash},
    matched against the run's recorded arm), for a run with a family the model SERVED names, a passing
    audit with no suspect calls
    and no prior context about these questions; a Codex run also needs a passing audit of its session
    logs, whose sub-agents' suspect calls count with the rest. A subfleet run also needs its arm's brief,
    unchanged, in what the lane gave the model, and a finish by DEADLINE; a run dispatched `--late`
    stays out. The operator may read a run's suspect calls, or its prior context, and list it in
    `accept_suspect` or `accept_prior` to pool it anyway."""
    runs = {}
    for d in run_dirs():
        resp = d / "response.json"
        if not resp.exists():
            continue
        meta = runners.read_meta(d)
        want = prompt_sha.get(meta.get("arm")) if isinstance(prompt_sha, dict) else prompt_sha
        if want is None or meta.get("prompt_sha256") != want:
            print(f"excluding {d.name}: made with prompt {str(meta.get('prompt_sha256'))[:12]}, not "
                  f"{str(want)[:12]} (arm {meta.get('arm')})", file=sys.stderr)
            continue
        if meta.get("backend") == "subfleet":
            why = _prompt_problem(d, meta)
            if why:
                print(f"excluding {d.name}: {why}", file=sys.stderr)
                continue
        if meta.get("late"):
            print(f"excluding {d.name}: dispatched after answers were collected (--late)", file=sys.stderr)
            continue
        finished = _utc(meta.get("finished_at"))
        if meta.get("backend") == "subfleet" and (finished is None or finished > _utc(DEADLINE)):
            print(f"excluding {d.name}: finished at {meta.get('finished_at')}, not by {DEADLINE}", file=sys.stderr)
            continue
        family = meta.get("family")
        if family is not None and meta.get("model_served") != SERVED.get(family):
            print(f"excluding {d.name}: a {family} run that {meta.get('model_served')} served, not "
                  f"{SERVED.get(family)}", file=sys.stderr)
            continue
        audit_path = d / "audit.json"
        try:
            result = json.loads(audit_path.read_text()) if audit_path.exists() else None
        except (ValueError, RecursionError):
            result = "unreadable"  # not an audit record (below)
        if result is None and not (allow_unaudited and meta.get("backend") == "manual"):
            print(f"excluding {d.name}: no audit (run `i86 audit`; manual runs need --allow-unaudited)",
                  file=sys.stderr)
            continue
        if result is not None and not _audit_record(result):
            print(f"excluding {d.name}: audit.json is not an audit record (run `i86 audit` again)", file=sys.stderr)
            continue
        if result is not None and not (result["passed"] and not result["violations"]):
            print(f"excluding {d.name}: audit failed", file=sys.stderr)
            continue
        suspect = result["suspect"] if result is not None else []
        if runners.served_by_codex(meta):
            session = _session_audit(d)
            if not isinstance(session, dict):
                print(f"excluding {d.name}: no readable audit of its session logs (run "
                      f"scripts/audit_codex_sessions.py {d.name})", file=sys.stderr)
                continue
            if not session["passed"] or session.get("violations"):
                print(f"excluding {d.name}: the audit of its session logs failed (read runs/{d.name}/{SESSIONS}/"
                      f"audit.json)", file=sys.stderr)
                continue
            suspect = _codex_suspects(suspect, session)
        if suspect and d.name not in accept_suspect:
            print(f"excluding {d.name}: {len(suspect)} suspect call(s) (read them in runs/{d.name}/audit.json and, "
                  f"for a Codex run, {SESSIONS}/audit.json; --accept-suspect {d.name} to include)", file=sys.stderr)
            continue
        fc = parse(g, json.loads(resp.read_text()))
        disclosed = str(fc.forecaster.get("prior_context") or "")
        if discloses(disclosed) and d.name not in accept_prior:
            print(f"excluding {d.name}: prior context mentions these questions (read it; --accept-prior "
                  f"{d.name} to include): {disclosed[:200]}", file=sys.stderr)
            continue
        runs[d.name] = fc
    return runs


def cmd_simulate(args) -> int:
    g = graph_mod.load()
    runs = accepted_runs(g, _prompt_sha(), args.allow_unaudited, set(args.accept_prior), set(args.accept_suspect))
    if not runs:
        raise SystemExit("no accepted runs: dispatch, collect and check first")
    result = pool(g, runs, n=args.n, seed=args.seed)
    meta = {d.name: runners.read_meta(d) for d in run_dirs() if d.name in runs}
    for rid, m in meta.items():
        m["prior_context"] = runs[rid].forecaster.get("prior_context")
        audit_path = runners.RUNS / rid / "audit.json"
        suspect = json.loads(audit_path.read_text()).get("suspect", []) if audit_path.exists() else None
        session = _session_audit(runners.RUNS / rid) if runners.served_by_codex(m) else None
        if isinstance(session, dict):
            suspect = _codex_suspects(suspect or [], session)
        if suspect is not None:
            m["suspect_calls"] = len(suspect)
    report.write(g, result, meta, OUTPUTS)
    print(f"pooled {len(runs)} run(s) into {OUTPUTS.relative_to(ROOT)}/")
    return 0


PREREG = ROOT / "prereg"


def cmd_prereg(args) -> int:
    # The registration (README.md), the map, the four briefs, the template, the context, the design, the
    # code that dispatches, audits, analyses and scores the runs, and the launchd jobs that run
    # scripts/deadline_guard.py and scripts/seal.py.
    files = [*ROOT.glob("README.md"), ROOT / "graph" / "graph.yaml",
             *sorted(p for p in PROMPT_DIR.glob("*") if p.is_file()), TEMPLATE_PATH,
             *sorted((ROOT / "context").iterdir()), *sorted(p for p in (ROOT / "design").glob("*") if p.is_file()),
             *ROOT.glob("pyproject.toml"), *sorted((ROOT / "src" / "i86").glob("*.py")),
             *sorted((ROOT / "scripts").glob("*.py")), *sorted((ROOT / "launchd").glob("*.plist"))]
    for d in run_dirs():
        files += [p for p in (d / "meta.json", d / "prompt.md", d / "prompt.sent.md", d / "response.md",
                              d / "response.json", d / REPAIRS, d / "problems.txt", d / "audit.json",
                              d / "transcript.jsonl") if p.exists()]
        # A Codex run's session logs, its sub-agents' included, and their audit
        # (scripts/audit_codex_sessions.py): the transcript records only the main thread.
        files += sorted(p for p in (d / "codex-sessions").glob("*") if p.is_file())
    files += sorted(p for p in OUTPUTS.glob("*") if p.is_file())
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT)}" for p in files]
    PREREG.mkdir(exist_ok=True)
    sums = PREREG / "SHA256SUMS"
    sums.write_text("\n".join(lines) + "\n")
    # The previous proof stamps the previous manifest, which its dated folder keeps. `ots stamp`
    # will not overwrite it, and left in place it would sit beside a manifest it does not prove.
    (PREREG / "SHA256SUMS.ots").unlink(missing_ok=True)
    print(f"{len(lines)} files hashed into {sums.relative_to(ROOT)}")
    if args.no_stamp:
        return 0
    proc = subprocess.run(["uvx", "--from", "opentimestamps-client", "ots", "stamp", str(sums)],
                          capture_output=True, text=True)
    print(proc.stdout + proc.stderr)
    return proc.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="i86", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prompt")
    a = sub.add_parser("assign")
    a.add_argument("--seed", type=int, default=SEED)
    a.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    d = sub.add_parser("dispatch")
    d.add_argument("backend", choices=["subfleet"], help="every run goes through Subfleet (design/assignment.json)")
    d.add_argument("label")
    d.add_argument("--tier", default="standard", choices=["standard", "hard"])
    d.add_argument("-m", "--model")
    d.add_argument("--late", action="store_true", help="dispatch after answers were collected (flagged in the report)")
    sub.add_parser("collect")
    sub.add_parser("check")
    sub.add_parser("audit")
    ab = sub.add_parser("abandon", help="mark a run that will never finish, so collection can proceed")
    ab.add_argument("label")
    rc = sub.add_parser("recover", help="take a lost subfleet run's final message from its own transcript")
    rc.add_argument("label")
    s = sub.add_parser("simulate")
    s.add_argument("--n", type=int, default=20000)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--allow-unaudited", action="store_true",
                   help="pool manual runs, which have no transcript to audit")
    s.add_argument("--accept-prior", action="append", default=[], metavar="LABEL",
                   help="pool this run although its prior_context mentions these questions (read it first)")
    s.add_argument("--accept-suspect", action="append", default=[], metavar="LABEL",
                   help="pool this run although its audit lists suspect calls (read them first)")
    pr = sub.add_parser("prereg")
    pr.add_argument("--no-stamp", action="store_true")
    args = ap.parse_args(argv)
    return {"prompt": cmd_prompt, "assign": cmd_assign, "dispatch": cmd_dispatch, "collect": cmd_collect, "check": cmd_check,
            "audit": cmd_audit, "abandon": cmd_abandon, "recover": cmd_recover, "simulate": cmd_simulate, "prereg": cmd_prereg}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
