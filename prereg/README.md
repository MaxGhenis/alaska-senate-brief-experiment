# Timestamps

`i86 prereg` writes `SHA256SUMS`, the SHA-256 of the registration (`README.md`), the map, the four
briefs and their hashes, the response template, the context brief, the assignment, `pyproject.toml`,
the package, the scripts and the launchd jobs. Once runs exist, it adds every run's `meta.json`,
`prompt.md`, `prompt.sent.md`, `response.md`, `response.json`, `repairs.json`, `problems.txt`,
`audit.json` and `transcript.jsonl`, where each exists, a Sol run's `codex-sessions/` folder, and
the files at the top of `outputs/`. It then stamps the manifest with OpenTimestamps. Each folder
below keeps one manifest and its proof; `SHA256SUMS` and `SHA256SUMS.ots` at the top hold the
latest.

To check a folder, run `shasum -a 256 -c prereg/<folder>/SHA256SUMS` from the repository's root at
the commit that added the folder, and `uvx --from opentimestamps-client ots verify
prereg/<folder>/SHA256SUMS.ots` once the stamp has a Bitcoin attestation.

| Folder | Written | Covers |
|---|---|---|
| `registration/` | 11:09Z 11/08 | the registration (`README.md`), the map, the four briefs, the template, the context, the assignment, the package configuration, the code and the launchd jobs, before the first dispatch |
| `collected/` | 13:38Z 11/08 | every run's answer, parsed response, repairs, audit and transcript, collected once the last run had finished |

## Sealing

The launchd fallback began a pass at 13:22Z on November 8, once the last run had ended. Its collect,
check and audit took about a minute, but its session audit, which reads the first line of each of
the lanes' 82,692 session logs, ran out of its 15 minutes. The plist sets no `ProcessType`, so
launchd applies light resource limits that throttle the job's CPU and I/O (`man launchd.plist`):
five minutes in, the audit had reached the 4,500th log. At 13:38Z, as soon as that pass let go of
the marker, I sealed by hand with `scripts/seal.py --now`, the path README's "Timing" names first.
Its session audit took 51 seconds, and it committed and pushed `collected/` at 13:38:58Z (68b0f2d).
A minute later the fallback found the record sealed and stopped.

## Deviations

Each entry gives its date and its reason. None changes the registered analysis, which
`outputs/arms/` holds as registered.

### 1. Two exec scripts the session audit misread (2026-11-08, before any analysis)

The session audit (`scripts/audit_codex_sessions.py`) fails a run when an exec script it ran
contains `require(`, `import(`, `fs.`, `Deno.`, `process.` or `Bun.` (`SCRIPT_ESCAPES`), since such
a script could reach files, the network or the process past the tools the audit reads. In the
sealed record it failed sol-04 (arm 11) and sol-08 (arm 01), each on one script and on nothing else:
no violation, no suspect call, no missing or orphaned thread, and every thread's own audit passed.
Each script checks the run's forecasts before the run answers, and each names a loop variable `fs`
(sol-04's `for(const [n,fs] of Object.entries(forecasts))`, sol-08's `for(const
[name,fs]of Object.entries(forecasts))`), so the pattern's `\bfs\.` matched `fs.all`. Neither
script reads or writes a file, reaches the network, starts a process or loads a module: besides
standard JavaScript, each calls only the exec tool's own built-ins, `store` and `text`, and in
sol-04 also `load`.

The registered analysis stands as registered: it leaves sol-04 and sol-08 out, as it leaves opus-03
out. This deviation adds one analysis beside it. As README's "Exclusions" does for suspect calls, I
hid each script's run label and arm, replaced every number in its forecasts with 0, and gave both
scripts to a separate reviewer, who cleared both (`outputs/flag-review/`). `scripts/deviation_1.py`
reruns `scripts/arms.py` unchanged and pools sol-04 and sol-08 as well (`outputs/arms-deviation-1/`).
It changes no registered file, and it stops if a named run's session audit failed on anything but
exec-script flags. Every other choice is the registered analysis's.

The reviewer's packet set the rule, admitting each run whose script it cleared, before the reviewer
replied (`outputs/flag-review/F2-F3-packet.md`). I wrote this entry after its reply and before
running any analysis. I had read both scripts in full, so I had seen those two runs' forecasts, but
no estimate.
