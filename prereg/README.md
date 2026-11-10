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
the lanes' 82,692 session logs, ran out of its 15 minutes: five minutes in, it had reached the
4,500th log. The plist sets no `ProcessType`, and launchd then applies light resource limits that
throttle a job's CPU and I/O (`man launchd.plist`). I didn't test whether those limits caused the
slow pass. At 13:38Z, as soon as that pass let go of
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

## Review after the analysis

On November 9, a separate Claude Fable 5.2 session reviewed the sealed record, Deviation 1 and all
four analyses, working read-only. It found nothing that changes a registered estimate. I repeated
its main checks:

- I reran `scripts/arms.py` and `scripts/deviation_1.py` into a scratch folder, and all 20 output
  files matched the committed ones byte for byte.
- `scripts/recompute.py` works from the sealed answers without the package, and its estimates,
  standard errors and degrees of freedom match all four analyses.
- `shasum -a 256 -c prereg/collected/SHA256SUMS` passes on all 272 lines.
- `scripts/exact_p.py` counts every re-assignment in place of the registered 100,000 draws
  (`outputs/exact-p/`, `outputs/exact-p-deviation-1/`). The exact p-values sit within 0.003 of the
  registered ones:

  | Analysis | P1 exact / registered | P2 exact / registered | E1 exact / registered |
  |---|---|---|---|
  | Registered | 0.530 / 0.530 | 0.489 / 0.490 | 0.308 / 0.311 |
  | Deviation 1 | 0.236 / 0.236 | 0.318 / 0.318 | 0.266 / 0.267 |

I confirmed each of the review's findings below in the sealed record. None changes the registered
analysis, and I treat every estimate they add as exploratory.

### 1. P2 measures where a step falls

In 16 of the 21 accepted runs, the `gov_dem_share` medians step up by 5.0 to 7.7 points between two
adjacent ranges, in 15 of them above 50%. The registered `jump` fits a line through three ranges on
each side, so it scores a step of D by where the step falls: D at 50%, −D/12 at 49.5% or 50.5%, and
−5D/12 at 49% or 51%. Ten runs step at 51%, and their jumps run from −2.3 to −3.1 points. No run
steps at 50%: across the two ranges beside it, no run's median moves more than 0.8 points.

So P2's estimate, +0.40 points (95% interval −0.76 to +1.56), compares where the runs with and
without the placebo block put their steps. With the block, the share of runs that step at 51%
exceeds the share without it by 0.125 (standard error 0.22), all of it among the Sol runs, and such
a step adds about 2.8 points of `abs_jump`. On the absolute difference between the two ranges beside
50%, the block's effect comes to +0.075 points (standard error 0.060). The governor's-race `jump`
means in `arm_means.csv`, −0.63 to −1.89 points, come from those steps: across 50% itself, each
arm's medians rise by 0.45 to 0.56 points, and no run's median falls.

No forecaster in any arm added a jump at 50% for the governor's race, so P2 cannot show whether the
placebo block's note on Measure 2 carries over to it. In E1, `contest` steps by 0.15 to 0.21 between
ranges away from 50% in 13 runs, so a small part of E1 also reflects where steps fall. P2 and E1
stand as registered.

### 2. `scripts/deviation_1.py` could have written over the registered outputs

The script read its output folder from the exact word `--out`. Given `--out=FOLDER`, it would have
added the deviation's note to `outputs/arms/summary.md`, and given no `--out`, it would have written
the deviation's analysis over `outputs/arms/`. I ran it only as its docstring gives, and the
committed outputs reproduce byte for byte. The script now requires `--out`, takes either spelling
and refuses the folders of the registered analysis and its two sensitivity checks.

It also admitted any named run whose flags all came from exec scripts. It now admits a run only if
each flag names the script the reviewer read: the session log, the line and the script's first 200
characters, as `outputs/flag-review/` keeps them. In the sealed record, sol-04's and sol-08's flags
match. With both changes, the script reproduces `outputs/arms-deviation-1/` byte for byte.

### 3. What the session audit fails a run on

README's "Exclusions" says that a Sol run stays out if `scripts/audit_codex_sessions.py` fails it.
The audit fails a run outright when an exec script matches a text pattern (`SCRIPT_ESCAPES`) or
calls a tool outside the audit's list, or when a log holds a tool or function call that the audit
doesn't read. `--accept-all-flags` accepts prior-context flags and suspect calls and never reaches
these, so the registered analysis had no way to admit sol-04 and sol-08.

The pattern looks for six names, so it would miss a script that reached past its tools through
another, such as `fetch(`, `eval(`, `Function(`, `globalThis`, a static `import` or `tools[...]`. I
didn't test which of those the exec tool's runtime provides. I searched all 497 exec scripts in the
12 Sol runs' 48 session logs: the pattern matches only the two scripts in Deviation 1, no script
uses any of those six, and no Sol thread ran a shell command.

### 4. sol-06 entered one answer the wrong way round

sol-06 gave `sullivan_post` 0.28 in the four ranges where Sullivan wins and 0.06 in the four where
he loses, the reverse of the other 20 accepted runs. Its sealed session log shows the slip
(`runs/sol-06/codex-sessions/rollout-2026-11-08T07-45-01-01a1bb30-d24b-74c0-9903-b3ccb6df8d80.jsonl`,
whose SHA-256 in `collected/SHA256SUMS` starts 43b69fe3). At line 134, a sub-agent advises about
0.25 to 0.30 after a defeat and 0.04 to 0.07 after reelection. At line 190, the run writes 0.28 for
the first four ranges and 0.06 for the last four, in a list that starts from Peltola's lowest share.
The review found no such slip in sol-06's other ten answers.

The registered E1 takes each answer's absolute jump, 0.22 either way round, so the slip leaves E1
unchanged. An estimate that signs each jump has to correct the answer first:
`outputs/power/summary.md` gives 0.012 with the correction and 0.023 without it.

### 5. The answer reader can miss a block that follows another fenced block

`extract_json` (`src/i86/response.py`) pairs fences wrongly when a fenced block with another
language tag, such as `python`, comes before the JSON block. It then falls back to the text from
the answer's first `{` to its last `}`, which fails to parse when the earlier block holds a brace.
README's "Repairs" says the reader takes the last bare or `json`-tagged fenced block that parses.
Each of the 24 sealed answers holds exactly one fenced block, so no run met the fault, and I leave
the reader as registered.

### 6. Deviation 1's record

The packet and the verdict for flags F2 and F3 entered git in one commit (ac545bc), and the
repository holds no transcript of the review. No public timestamp separates the two files, so the
record cannot show that the packet set its rule before the reviewer replied.

The session audit's pattern failed 2 of the 6 Sol runs in the placebo block's arms and none of the
other 6 (Fisher's exact p = 0.45). Both admitted runs step at 51% in the governor's race, which
moves P2 from +0.40 in the registered analysis to +0.50 in Deviation 1.

### 7. More runs of these two models

`scripts/power.py` asks what more runs could have changed (`outputs/power/`,
`outputs/power-deviation-1/`). If all three true effects equal the registered estimates, the
registered rule reaches an 80% chance of rejecting at about 416 runs for P1, 304 for P2 and 240 for
E1, against 21. A primary whose effect stood alone, with the other two at zero, would need about
544, 376 and 272. The 21 runs would detect, 80% of the time, an E1 change of 0.042, a third of the
index without the estimand block (0.131).
