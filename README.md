# Alaska's Senate count and the forecaster's brief: a randomized test

In the Alaska Division of Elections' unofficial count of the 2026 US Senate race, Mary Peltola leads
Senator Dan S. Sullivan by 637 first-choice votes, 102,962 to 102,325. The Division last updated that
count on November 4, with 400 of 403 precincts reporting, and counts absentee, early and questioned
ballots next, on November 10. Before that count, 24 AI forecasters, 12 runs each of Claude Opus 5.5
and GPT-6.1 Sol, forecast the count, the courts, the Senate seat and Alaska's economy at eight ranges
of Peltola's final share. Each forecaster gets one of four briefs at random. The briefs add or leave
out two blocks of text, and I measure what each block does to the jump in the forecasts where
Peltola's share crosses 50%.

## Why randomize the brief

Before polls closed on November 3, AI forecasters gave conditional forecasts for DC's Initiative 86
([MaxGhenis/dc-i86-llm-forecast](https://github.com/MaxGhenis/dc-i86-llm-forecast)) and for the Texas
governor's race
([MaxGhenis/texas-governor-data-center-forecasts](https://github.com/MaxGhenis/texas-governor-data-center-forecasts)),
and their briefs framed the cutoff differently. The DC brief told forecasters that its two cells
beside 50% "differ almost only in whether the measure passed", so that "the difference between them is
your estimate of what a bare pass changes". It also told them that DC's result "cannot change
Denver's", so Denver's same-day vote should show no jump. The Texas brief said neither. So the DC
study's Denver placebo can't test whether forecasters add jumps where none belong, since its brief
told them where to make none, and neither study can show what stating the estimand does. Randomizing
both blocks within one race separates them from what else differed between the two studies: the race,
the questions and the date.

## Design

**The result.** Every question conditions on Peltola's share of the votes for Peltola and Dan S.
Sullivan in the last round of the certified ranked-choice tabulation, after any recount. If a
candidate wins a first-choice majority, so that no tabulation runs, the first round counts as the
last. A tie, or a last round without both Peltola and Sullivan, voids every forecast.

**Ranges.** Forecasters answer at eight ranges of that share: at or below 48.5% (`dem_to_48.5`), six
half-point ranges from 48.5% to 51.5% (`dem_48.5_49.0` through `dem_51.0_51.5`), and above 51.5%
(`dem_over_51.5`). Each range takes the shares above its lower end up to its upper end, except that
`dem_49.5_50.0` stops short of 50%. The two ranges beside 50% differ by half a point of Peltola's
share and by who won: Sullivan narrowly in `dem_49.5_50.0`, Peltola narrowly in `dem_50.0_50.5`.
Peltola holds 50.2% of the first-choice votes for the two before the late ballots. Shares far from
that would take lopsided late ballots, a counting error or a court fight, and a forecaster who
pictures those routes can answer the far ranges for reasons unrelated to who won. So the fitted lines
below use only the six closed ranges, and once the Division certifies the result, `scripts/score.py`
scores every run's forecasts for the one range that holds it.

**Questions.** `graph/graph.yaml` defines twelve questions. Forecasters give one distribution for
Peltola's share (`dem_share`) and answer the other eleven at all eight ranges, 89 answers in all:

- The vote: Ballot Measure 2's Yes share (`bm2_yes_share`), on repealing the open primary and
  ranked-choice general elections, and the Kreiss-Tomkins/Johnson ticket's share of the last round for
  governor (`gov_dem_share`).
- The count and the courts: by December 31, a recount request or order (`recount`), an election
  contest in superior court (`contest`) and a court order that stops, delays or undoes certification
  (`court_order`); and by January 31, 2027, the certified winner's oath (`winner_sworn`).
- The seat: Peltola in the Senate on February 1, 2027 (`peltola_senator`), the Democratic caucus's
  size that day (`dem_caucus`), and the President naming Sullivan to a federal executive or judicial
  post by June 30, 2027 (`sullivan_post`).
- Alaska's economy: the seasonally adjusted unemployment rate for December 2026 (`ak_unemployment`)
  and crude oil production in January 2027 (`ak_oil`).

Each question names its resolution source in the map, and `context/brief.md` gives every forecaster
the count so far, the Division's calendar and the court cases, with sources.

**Placebos.** The same ballots decide the Senate race, Ballot Measure 2 and the governor's race, so
the Senate result can't change the other two results. Across the two ranges beside 50%, a forecaster's
answers for them should move only by what half a point of Peltola's share says about voters, with no
added jump for who won. The placebo block says so for Measure 2; no brief says it for the governor's
race. I also use `peltola_senator` as a check: a forecaster who conditions on the ranges as asked
gives it a jump near 1.

**The two blocks.** Each brief's rule 4 defines the ranges, and in every arm it says:

> Everything else about the world is whatever you think is consistent with that result.

The estimand block continues that sentence:

> …, so a share above 51.5% can imply stronger support for Peltola than one just above 50%. The
> `dem_49.5_50.0` and `dem_50.0_50.5` ranges differ almost only in who won; the difference between
> them is your estimate of what a bare win changes.

The placebo block adds a sentence to rule 4:

> The same ballots decide Ballot Measure 2, so the Senate result cannot change its result: there the
> two ranges should differ only by what half a point of Peltola's share says about voters.

and a note to the Measure 2 question:

> Forecast at each range so the two results can be correlated. The same ballots decide both, so the
> Senate result cannot cause Measure 2's: the `dem_49.5_50.0` and `dem_50.0_50.5` ranges should
> differ only by what half a point of Peltola's share says about voters.

The DC brief carried both blocks, with its cells and Denver in place of these ranges and Measure 2,
and the Texas brief carried neither.

**Arms.** Four arms cross the blocks, with the first digit for the estimand block and the second for
the placebo block. Arm 00 adds neither, as the Texas brief did, and arm 11 adds both, as the DC brief
did; arm 10 adds only the estimand block, and arm 01 only the placebo block. `prompts/arm-00.md`
through `prompts/arm-11.md` hold the four briefs, which match byte for byte outside the blocks.

**Assignment.** `i86 assign` drew the arms with seed 20261108 into `design/assignment.json`: three
runs of each model in each arm, 24 in all. I dispatch them in that file's order, which alternates Opus
and Sol, Opus first. `i86 dispatch` refuses a label the file doesn't list, a file that doesn't match
its seed, and a second dispatch of any label.

**Forecasters.** Claude Opus 5.5 runs on Subfleet's Claude lanes (`--tier standard -m opus`) and
GPT-6.1 Sol on its Codex lanes (`--tier hard`). Each run works alone, with web search, in an empty
directory and a read-only sandbox.

## Registered analysis

**Measures.** For each run and each question it answers at every range, `src/i86/arms.py` reads the
run's answer at each range, the probability for a yes/no question and the median for a quantity, and
computes:

- `jump`: the gap at 50% between two least-squares lines, Peltola's minus Sullivan's. One line runs
  through the three closed Sullivan-win ranges at their midpoints (48.75%, 49.25% and 49.75%), the
  other through the three closed Peltola-win ranges (50.25%, 50.75% and 51.25%).
- `abs_jump`: the jump's absolute value.
- `contrast`: the answer at `dem_50.0_50.5` minus the answer at `dem_49.5_50.0`.
- `slope`: the mean of the two lines' slopes, per point of Peltola's share.

**Primary outcomes.** I register three, each with a two-sided test:

1. **P1, the coached placebo**: the placebo block's main effect on the abs_jump of `bm2_yes_share`. I
   expect the block to shrink that jump, since it tells forecasters to make none.
2. **P2, the uncoached placebo**: the placebo block's main effect on the abs_jump of `gov_dem_share`.
   Forecasters may carry the Measure 2 note over to the governor's race or keep it to Measure 2, so I
   expect no particular sign.
3. **E1, the effect index**: the estimand block's main effect on each run's mean abs_jump over
   `recount`, `contest`, `court_order`, `winner_sworn` and `sullivan_post`, the questions where who
   won can change the answer through the count, the courts and Sullivan's next job. I expect the block
   to widen those jumps, since it tells forecasters that the two ranges beside 50% differ almost only
   in who won.

**Estimates.** An arm's mean weights the two models equally: the mean of the Opus runs' mean and the
Sol runs' mean. A main effect subtracts the mean of the two arms without a block from the mean of
the two arms with it, and the interaction subtracts arms 10 and 01 from arms 11 and 00. For each
model, `src/i86/arms.py` pools the variance of its runs about their arms' means, with one degree of
freedom for each run less one for each arm. Each estimate's standard error combines the two models'
pooled variances, and its 95% t interval takes Welch-Satterthwaite degrees of freedom. If one model
has no accepted run in some arm, `scripts/arms.py` leaves that model out of every estimate and uses
the other model's runs alone. If neither model has an accepted run in every arm, it reports every
estimate as undefined. For its p-value, `scripts/arms.py` re-assigns the arms among each model's
runs 100,000 times, keeping each arm's count. The p-value gives the share of those assignments,
counting the observed one, whose effect lies at least as far from zero as the observed effect. The
randomization makes that test exact, whatever the answers' distribution, under the sharp null that
the blocks change neither any run's answers nor whether the run stays in the analysis. Holm's
step-down correction adjusts the three primaries' p-values, and counts an undefined one among the
three without adjusting it. I reject a primary effect's null when its adjusted p-value falls below
0.05.

**Other estimates.** `scripts/arms.py` also reports each primary effect within each model, with its
own interval. Beyond the primaries, it estimates the effect of each block and of their interaction on
every question's four measures, with 20,000 re-assignments each and no correction for multiple
comparisons. I treat all of these as exploratory. `arm_means.csv` gives every measure's mean by arm,
the `peltola_senator` check among them.

**Exclusions.** A run stays out of every analysis if:

- its answer fails `i86 check`, after the two repairs below;
- it ran on a brief other than its arm's current one, or the prompt that Subfleet records sending
  lacks that brief byte for byte;
- a model other than its own served it, by Subfleet's record: `claude-opus-5-5` for an Opus run,
  `gpt-6.1-sol` for a Sol run;
- `i86 audit` fails it, or, for a Sol run, `scripts/audit_codex_sessions.py` fails any thread behind
  it;
- it fails or never starts, or Subfleet records it finishing after 12:00Z on November 10;
- I dispatched it with `i86 dispatch --late`, after `i86 collect` had copied answers in.

Each label gets one dispatch, and no run replaces another. Before `i86 check` parses an answer, it
makes two repairs, the same in every arm, and records each in `runs/<label>/repairs.json`. It drops
each comma that comes just before a closing brace or bracket, outside strings, when the JSON parses
only without those commas. It also drops each key the template lacks, among the questions or in a
question's forecasts, when the key holds null, text, a number, true, false or a list, none of which
can hold a forecast. Seven Opus answers failed the check in the DC and Texas studies, and these
repairs would have let six of them pass. Any other fault still fails the run, and `scripts/arms.py
--strict` reruns the analysis without the runs whose answers `i86 check` repaired.

A run with a suspect tool call, or whose `prior_context` mentions these questions, also stays out
unless I accept the flag. Before the analysis runs, I hide each flag's run label and arm and cut any
quote of either block from it. A separate model reviewer then reads each flag, and I accept the flags
it clears. As a sensitivity check, `scripts/arms.py --accept-all-flags` reruns the analysis accepting
every flag and keeps every other exclusion.

## Scoring

Every brief tells forecasters how I score their answers (rule 11):

> Your forecasts are scored. Once the result is certified, after any recount, the range it falls in
> is known, and each question's forecast for that range is scored against what happens: a probability
> by its Brier score, a quantity by the quantile score of your five quantiles, and `dem_share` by its
> ranked probability score. Forecasts for the other seven ranges are void and are not scored.

`scripts/score.py` applies that rule against `outcomes/outcomes.yaml` as questions resolve, through
mid-2027: (p − y)² for a probability; the mean pinball loss over the five quantiles for a quantity;
and for `dem_share`, the sum over the seven range ends e of (F(e) − 1[y ≤ e])². It reports each arm's
mean score and each block's effect on it, as exploratory results. A score checks a run's answers at
the one range that happens, so it can neither confirm nor rule out a jump.

## Timing

The Division's November 10 count brings its first new results since election week, so every run must
finish before then. `scripts/deadline_guard.py` cancels any run still waiting for a lane at 09:00Z
on November 10, kills any still going at 12:00Z (3 a.m. in Juneau), and marks each one abandoned.
Voting ended on November 3, so, unlike the DC and Texas briefs, this brief lets forecasters use vote
counts, polls and prediction markets. Once the last run has finished, `scripts/seal.py --now` runs
`i86 recover` on every run that Subfleet reports lost, whatever its arm. Where such a run's agent
finished cleanly, `i86 recover` takes its answer from the run's own transcript. `scripts/seal.py`
then collects, checks and audits every answer, audits every thread behind each Sol run, and stamps
the record. A launchd copy of it does the same from 12:00Z on November 8 if no one has, tries again
every minute until it has committed and pushed the record, and gives up at 16:00Z on November 10,
7 a.m. in Juneau.

## Registration

Before the first dispatch, `i86 prereg` hashes this README, the map, the four briefs, the response
template, the context, the assignment, the package configuration and the code, and OpenTimestamps
stamps the hashes; `prereg/registration/` keeps both. The stamp dates the hypotheses, measures, tests
and exclusion rules above. Once the last run finishes, `prereg/collected/` will keep the same record
for every run's answer, audit and transcript, with a stamp from before the November 10 count.

## Independence

Each forecaster gets its arm's brief byte for byte: `prompts/SHA256SUMS` lists the four hashes, and
each run's `meta.json` records its own. Each run works in an empty directory under
`~/.cache/aksen-work/` that takes the run's label as its name, which says nothing about the arm, and
the brief tells forecasters not to read local files. I keep this repository private until the last
run finishes, then make it public, and `i86 collect` copies no answer into it before then. `i86
audit` reads each run's transcript and flags any tool call that could read outside the run's work
directory. Codex returns only a Sol run's main thread, so `scripts/audit_codex_sessions.py` finds
every thread behind the run, its sub-agents' among them, in its lane's session logs and audits each
one the same way. Those logs carry the lanes' account details, so they stay out of git, and the
stamped manifests keep their hashes.

## Power and limits

Each main effect compares 12 runs with 12. At 5% two-sided, a test this size detects an effect of
about 1.22 run-level standard deviations 80% of the time. Holm's correction compares the smallest of
the three primaries' p-values with 5%/3, and at that level the test needs about 1.45 standard
deviations for the same power. Runs of one model share its training and often its answers, so the
intervals cover further runs of these two models, not other forecasters. This study tests the blocks
on one race, with its own questions and date, and reruns neither the DC study nor the Texas one.
Models produced these forecasts under stated scenarios: a jump measures what a forecaster expects a
bare win to change, and the certified result will show only one side of 50%.

## Run it

```bash
uv venv --python 3.13
uv pip install -e ".[dev]"
.venv/bin/i86 prompt                          # regenerate the four briefs from the map and context/
.venv/bin/pytest -q -n 4
```

```bash
.venv/bin/i86 dispatch subfleet opus-01 --tier standard -m opus   # every label in design/assignment.json
.venv/bin/i86 dispatch subfleet sol-01 --tier hard
.venv/bin/i86 collect                         # once every run has finished
.venv/bin/i86 abandon LABEL                   # mark a run that will never finish; final
.venv/bin/i86 check                           # parse and validate every answer
.venv/bin/i86 audit                           # check every transcript's tool calls
.venv/bin/python scripts/audit_codex_sessions.py   # audit every thread behind each Sol run
.venv/bin/python scripts/arms.py              # the registered analysis, into outputs/arms/
.venv/bin/python scripts/arms.py --accept-all-flags --out outputs/arms-all-flags
.venv/bin/python scripts/arms.py --strict --out outputs/arms-strict   # without the repaired answers
.venv/bin/python scripts/score.py             # scores, into outputs/scores/, as outcomes resolve
.venv/bin/i86 prereg                          # hash everything into prereg/SHA256SUMS and stamp it
```

The audit's tests compare its shell model with macOS's shells and BSD tools, so the suite assumes
macOS with ripgrep and poppler from Homebrew.

## Files

- `graph/graph.yaml`: the questions, ranges and resolution criteria.
- `context/brief.md`: the facts every forecaster gets, with sources.
- `prompts/arm-00.md` through `prompts/arm-11.md`: the four briefs, with their hashes in
  `prompts/SHA256SUMS`; `schemas/template.json`: the response template.
- `design/assignment.json`: each run's model, arm and dispatch order.
- `runs/<label>/`: each run's brief, the prompt its lane sent, metadata, verbatim answer, transcript,
  parsed answer, any repairs `i86 check` made, and audit, and for a Sol run,
  `codex-sessions/audit.json` with the audit of every thread.
- `src/i86/`: the map, briefs, answer checks, measures and effects (`arms.py`), scoring (`score.py`),
  runners and audit. The DC study's simulator stays for its tests; `i86 simulate` refuses this map.
- `scripts/`: `arms.py` and `score.py` write `outputs/arms/` and `outputs/scores/`;
  `deadline_guard.py` and `seal.py` enforce the deadline and seal the answers;
  `audit_codex_sessions.py` audits every thread of a Sol run.
- `outcomes/outcomes.yaml`: each outcome, with its date and source, as it resolves.
- `prereg/`: hashes and timestamps.
- `tests/`: tests with MOCK answers. Most run on the DC Initiative 86 map, kept as
  `tests/fixtures/i86_graph.yaml`; the brief, measure and scoring tests run on this map.

## Provenance

I forked this repository from
[MaxGhenis/texas-governor-data-center-forecasts](https://github.com/MaxGhenis/texas-governor-data-center-forecasts)
at cb2d06b, itself a fork of
[MaxGhenis/dc-i86-llm-forecast](https://github.com/MaxGhenis/dc-i86-llm-forecast) at c6dc25b. The
package keeps the name `i86` and the ballot-measure vocabulary of the DC map: `fail` ranges hold
Sullivan wins, `pass` ranges hold Peltola wins, and the map's conditions say `passes` for a Peltola
win.

## License

MIT. See [LICENSE](LICENSE).
