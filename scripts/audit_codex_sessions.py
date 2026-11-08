"""Audit every Codex thread behind a run, the sub-agents it spawned included, from the lanes' logs.

On Codex CLI 0.159, `codex exec --json`, which `runs/<label>/transcript.jsonl` records, carries only
the main thread. The sub-agents that thread spawns (`spawn_agent`) run their own web searches and
shell commands, and only the lane's session logs (`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`)
record them. So `i86 audit`, which reads the transcript, never sees their calls. For each Codex run
this script:

1. finds the run's main thread (a session `codex exec` started in the run's work directory) and every
   thread spawned from it, at any depth, in the lanes' logs and the copies already kept (so a rerun
   after a lane prunes its logs gives the same audit), and checks that each thread a log says it
   started has a log;
2. copies those logs into `runs/<label>/codex-sessions/`, never replacing a different file;
3. turns each log's completed items into the `codex exec --json` items `i86 audit` reads (web searches,
   shell commands, messages) and audits them with `audit.audit_transcript`, as `i86 audit` audits a
   transcript;
4. lists every tool the threads called, and flags any it does not read: an `exec` script tool other
   than `web__run` and `exec_command`, a function other than the sub-agent ones, an item of a kind it
   does not convert, or a command run outside the work directory;
5. writes `runs/<label>/codex-sessions/audit.json`.

    .venv/bin/python scripts/audit_codex_sessions.py [LABEL ...] [--lanes DIR]

It exits 1 if any thread fails the audit or any check above flags something.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from i86 import audit, runners  # noqa: E402

RUNS = ROOT / "runs"
LANES = Path.home() / ".subfleet" / "lanes"
FOLDER = "codex-sessions"
SCRIPT_TOOLS = {"web__run", "exec_command"}  # what an `exec` script may call: the web, and the shell
# The collaboration tools Codex's multi-agent role prompt names
FUNCTIONS = {"spawn_agent", "send_message", "followup_task", "wait_agent", "interrupt_agent", "list_agents"}
SKIPPED_ITEMS = {"UserMessage", "SubAgentActivity"}  # the brief or a thread's message; sub-agent events
SCRIPT_ESCAPES = re.compile(r"\brequire\s*\(|\bimport\s*\(|\bfs\.|\bDeno\.|\bprocess\.|\bBun\.")


def header(path: Path) -> dict:
    """A session log's first record, its `session_meta` payload ({} if it has none)."""
    with path.open() as fh:
        first = json.loads(fh.readline() or "{}")
    if not isinstance(first, dict) or first.get("type") != "session_meta":
        return {}
    payload = first.get("payload")
    return payload if isinstance(payload, dict) else {}


def read_headers(paths: list[Path]) -> list[tuple[Path, dict]]:
    """Each readable log among `paths`, in order, with its header. Read once per invocation: the lanes
    keep tens of thousands of logs, and every run's audit looks through all of them."""
    out = []
    for path in paths:
        try:
            out.append((path, header(path)))
        except (OSError, ValueError):
            continue
    return out


def lane_logs(lanes: Path) -> list[Path]:
    return sorted(lanes.glob("*/sessions/*/*/*/rollout-*.jsonl"))


def spawned_by(meta: dict) -> str | None:
    """The thread that spawned this one, or None for a thread `codex exec` started."""
    src = meta.get("source")
    if isinstance(src, dict):
        return (src.get("subagent") or {}).get("thread_spawn", {}).get("parent_thread_id")
    return None


def find_threads(paths: list[Path], workdir: str, heads: list[tuple[Path, dict]] | None = None) -> dict[str, dict]:
    """Every session log among `paths` (or the logs `heads` has read already) whose thread worked in
    `workdir`, by thread id; a thread's first log wins."""
    found = {}
    for path, meta in (read_headers(paths) if heads is None else heads):
        if meta.get("cwd") != workdir or not meta.get("id") or meta["id"] in found:
            continue
        found[meta["id"]] = {"path": path, "spawned_by": spawned_by(meta), "source": meta.get("source"),
                             "started": meta.get("timestamp"), "agent_path": (
                                 ((meta.get("source") or {}).get("subagent") or {}).get("thread_spawn", {})
                                 .get("agent_path") if isinstance(meta.get("source"), dict) else None)}
    return found


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def place(dest: Path, fill) -> None:
    """Write `dest` whole or not at all: `fill(tmp)` writes a hidden file in the run's folder, above
    codex-sessions/, whose every file `i86 prereg` hashes; os.replace then moves it in."""
    tmp = dest.parent.parent / f".{dest.name}.{os.getpid()}.tmp"
    try:
        fill(tmp)
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def keep(src: Path, dest_dir: Path) -> Path:
    """Copy a log into the run's folder, append-only: an existing copy must match byte for byte. The
    copy lands whole or not at all, so a pass cut off midway leaves nothing a later pass refuses."""
    dest = dest_dir / src.name
    if dest.exists():
        if sha256(dest) != sha256(src):
            raise SystemExit(f"{dest} differs from {src}; the kept copy is final")
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    place(dest, lambda tmp: shutil.copyfile(src, tmp))
    return dest


def local_path(cwd) -> str | None:
    """A CommandExecution's cwd (a file:// URL or a path) as a path."""
    if not isinstance(cwd, str) or not cwd:
        return None
    return unquote(urlparse(cwd).path) if cwd.startswith("file:") else cwd


def convert(path: Path, workdir: str) -> tuple[list[dict], dict]:
    """The log's completed items as `codex exec --json` lines, and what the conversion found."""
    lines: list[dict] = []
    found = {"items": collections.Counter(), "script_tools": collections.Counter(),
             "functions": collections.Counter(), "started_threads": [], "flags": []}
    with path.open() as fh:
        for n, raw in enumerate(fh, 1):
            rec = json.loads(raw)
            payload = rec.get("payload") or {}
            if rec.get("type") == "response_item" and payload.get("type") == "custom_tool_call":
                name = payload.get("name")
                if name != "exec":
                    found["flags"].append(f"line {n}: custom tool {name!r}, which this script does not read")
                    continue
                code = str(payload.get("input", ""))
                for tool in re.findall(r"\btools\.(\w+)", code):
                    found["script_tools"][tool] += 1
                    if tool not in SCRIPT_TOOLS:
                        found["flags"].append(f"line {n}: an exec script calls tools.{tool}")
                if SCRIPT_ESCAPES.search(code):
                    found["flags"].append(f"line {n}: an exec script reaches past its tools: {code[:200]!r}")
            elif rec.get("type") == "response_item" and payload.get("type") == "function_call":
                name = payload.get("name")
                found["functions"][name] += 1
                if name not in FUNCTIONS:
                    found["flags"].append(f"line {n}: function {name!r}, which this script does not read")
            if rec.get("type") != "event_msg" or payload.get("type") != "item_completed":
                continue
            item = payload.get("item") or {}
            kind = item.get("type")
            found["items"][f"{kind}:{item.get('kind')}" if kind == "Extension" else kind] += 1
            if kind == "Extension" and str(item.get("kind", "")).startswith("web."):
                lines.append({"type": "item.completed", "item": {
                    "id": item.get("id"), "type": "web_search", "query": item.get("query", ""),
                    "action": item.get("action"), "results": item.get("results")}})
            elif kind == "CommandExecution":
                command = item.get("command")
                cwd = local_path(item.get("cwd"))
                if cwd is not None and cwd.rstrip("/") != workdir.rstrip("/"):
                    found["flags"].append(f"line {n}: a command ran in {cwd}, outside the work directory")
                lines.append({"type": "item.completed", "item": {
                    "id": item.get("id"), "type": "command_execution",
                    "command": shlex.join(command) if isinstance(command, list) else command,
                    "aggregated_output": item.get("aggregated_output", ""),
                    "exit_code": item.get("exit_code"), "status": item.get("status")}})
            elif kind == "AgentMessage":
                text = " ".join(p.get("text", "") for p in item.get("content") or [] if isinstance(p, dict))
                lines.append({"type": "item.completed", "item": {"id": item.get("id"), "type": "agent_message",
                                                                  "text": text}})
            elif kind == "Reasoning":
                lines.append({"type": "item.completed", "item": {"id": item.get("id"), "type": "reasoning",
                                                                  "text": ""}})
            elif kind == "SubAgentActivity":
                if item.get("kind") == "started" and item.get("agent_thread_id"):
                    found["started_threads"].append(item["agent_thread_id"])
            elif kind not in SKIPPED_ITEMS:
                found["flags"].append(f"line {n}: a completed {kind!r} item, which this script does not read")
    return lines, found


def audit_thread(path: Path, workdir: str) -> tuple[dict, dict]:
    lines, found = convert(path, workdir)
    with tempfile.TemporaryDirectory() as tmp:
        stream = Path(tmp) / "stream.jsonl"
        stream.write_text("".join(json.dumps(line) + "\n" for line in lines))
        result = audit.audit_transcript(stream, workdir)
    return result, found


def audit_run(run_dir: Path, lanes: Path, lane_heads: list[tuple[Path, dict]] | None = None) -> dict:
    """Audit one run. `lane_heads`, the lanes' logs with their headers, is read here when not given."""
    meta = json.loads((run_dir / "meta.json").read_text())
    workdir = meta.get("workdir", "")
    if lane_heads is None:
        lane_heads = read_headers(lane_logs(lanes))
    heads = [*lane_heads, *read_headers(sorted((run_dir / FOLDER).glob("rollout-*.jsonl")))]
    threads = find_threads([], workdir, heads) if workdir else {}
    roots = [t for t, v in threads.items() if v["spawned_by"] is None]
    report = {"run": run_dir.name, "workdir": workdir, "threads": [], "missing": [], "flags": [],
              "orphans": sorted(t for t, v in threads.items() if v["spawned_by"] and v["spawned_by"] not in threads)}
    if len(roots) != 1:
        report["flags"].append(f"{len(roots)} threads started by codex exec in the work directory; expected 1")
    started: set[str] = set()
    for tid, info in sorted(threads.items(), key=lambda kv: kv[1]["started"] or ""):
        kept = keep(info["path"], run_dir / FOLDER)
        result, found = audit_thread(kept, workdir)
        started.update(found["started_threads"])
        report["flags"] += [f"{kept.name} {flag}" for flag in found["flags"]]
        report["threads"].append({
            "thread_id": tid, "spawned_by": info["spawned_by"], "agent_path": info["agent_path"],
            "started": info["started"], "file": f"{FOLDER}/{kept.name}", "sha256": sha256(kept),
            "items": dict(found["items"]), "script_tools": dict(found["script_tools"]),
            "functions": dict(found["functions"]), "audit": result})
    report["missing"] = sorted(started - set(threads))
    report["web_calls"] = sum(t["audit"]["web_calls"] for t in report["threads"])
    report["commands"] = sum(t["items"].get("CommandExecution", 0) for t in report["threads"])
    report["violations"] = sum(len(t["audit"]["violations"]) for t in report["threads"])
    report["suspect"] = sum(len(t["audit"]["suspect"]) for t in report["threads"])
    report["passed"] = (bool(report["threads"]) and not report["missing"] and not report["orphans"]
                        and not report["flags"] and all(t["audit"]["passed"] for t in report["threads"]))
    (run_dir / FOLDER).mkdir(parents=True, exist_ok=True)
    place(run_dir / FOLDER / "audit.json", lambda tmp: tmp.write_text(json.dumps(report, indent=1) + "\n"))
    return report


def codex_runs() -> list[Path]:
    """Collected runs a Codex lane served."""
    out = []
    for d in sorted(RUNS.iterdir()):
        meta_path = d / "meta.json"
        if not meta_path.is_file() or not (d / "response.md").exists():
            continue
        if runners.served_by_codex(json.loads(meta_path.read_text())):
            out.append(d)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("labels", nargs="*", help="runs to audit (default: every collected Codex run)")
    ap.add_argument("--lanes", type=Path, default=LANES, help="the Subfleet lanes folder")
    args = ap.parse_args(argv)
    dirs = [RUNS / label for label in args.labels] if args.labels else codex_runs()
    lane_heads = read_headers(lane_logs(args.lanes)) if dirs else []
    worst = 0
    for d in dirs:
        r = audit_run(d, args.lanes, lane_heads)
        helpers = sum(1 for t in r["threads"] if t["spawned_by"])
        print(f"{r['run']}: {'ok' if r['passed'] else 'FAILED'}; {len(r['threads'])} threads ({helpers} sub-agents), "
              f"{r['web_calls']} web calls, {r['commands']} commands, {r['violations']} violations, "
              f"{r['suspect']} suspect, {len(r['missing'])} missing, {len(r['flags'])} flags")
        for t in r["threads"]:
            for rec in t["audit"]["violations"] + t["audit"]["suspect"]:
                print(f"    {t['thread_id'][:13]} {rec.get('why', '')}: {rec['call'][:160]}")
        for flag in r["flags"]:
            print(f"    {flag}")
        worst = max(worst, 0 if r["passed"] else 1)
    return worst


if __name__ == "__main__":
    sys.exit(main())
