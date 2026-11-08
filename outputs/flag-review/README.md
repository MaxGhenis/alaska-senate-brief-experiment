# Flag review

Three flags reached the analysis. Before it ran, I hid each flagged run's label and arm, checked that
no flag quotes either block, and gave the flags to separate reviewers: Claude Opus 5.5
(`claude-opus-5-5`), each in a fresh context and told to judge the text alone. Neither reviewer read
a file or ran a command; each one's only tool call returned its verdict.

| Flag | Run | What raised it | Packet | Verdict |
|---|---|---|---|---|
| F1 | opus-02 | its `prior_context` matched the disclosure check on "Alaska" | `F1-packet.md` | `F1-verdict.md`: CLEAR |
| F2 | sol-04 | the session audit's `fs.` check matched an exec script's loop variable | `F2-F3-packet.md`, Script A | `F2-F3-verdict.md`: CLEAR |
| F3 | sol-08 | the same check, in another script | `F2-F3-packet.md`, Script B | `F2-F3-verdict.md`: CLEAR |

`i86 audit` found no suspect call in any of the 24 transcripts, and the session audit found none in
the 48 threads behind the 12 Sol runs. Every other run wrote a bare "none" as its `prior_context`.

F1 follows the registered procedure: the registered analysis accepts it (`--accept-prior opus-02`).
With no other prior-context flag and no suspect call, the sensitivity check `--accept-all-flags` pools
the same runs.

F2 and F3 differ. The session audit fails a run outright on such a flag, so the registered analysis
leaves sol-04 and sol-08 out whatever a reviewer says. Deviation 1 in `prereg/README.md` adds an
analysis that pools them, since the reviewer cleared both scripts.

`sol-04-script.js` and `sol-08-script.js` hold the two scripts as the runs wrote them: line 155 of
sol-04's `codex-sessions/rollout-2026-11-08T07-44-15-01a1bb30-1da0-7b61-bb0e-a2940be1ab9e.jsonl` and
line 204 of sol-08's `codex-sessions/rollout-2026-11-08T07-46-17-01a1bb31-faec-7e52-aeab-dac310a4186b.jsonl`.
`.gitignore` keeps the session logs out of git, and the sealed manifest (`prereg/collected/SHA256SUMS`)
lists each log's hash. The packet replaced every number in each script's `forecasts` object with 0,
since the forecasts could hint at the arm.
