# What more runs could change

Exploratory and post hoc: a planning calculation from `outputs/arms-deviation-1/per_run.csv` by `scripts/power.py`, not part of the registered analysis. These are model forecasts under stated scenarios, not observations.

## Today's estimates

| primary | runs | estimate | standard error | mean without the block | randomization p | Holm p |
|---|---|---|---|---|---|---|
| P1 coached placebo | 23 | 0.0181 | 0.0157 | 0.0222 | 0.236 | 0.707 |
| P2 uncoached placebo | 23 | 0.496 | 0.475 | 1.27 | 0.318 | 0.707 |
| E1 effect index | 23 | 0.0102 | 0.01 | 0.13 | 0.267 | 0.707 |

## The smallest effect a study detects 80% of the time

Analytic, at 5%/3, the level Holm's correction applies to the smallest of three p-values. The first column uses today's standard error and degrees of freedom; the others, balanced studies.

| primary | today's runs | 24 runs | 48 runs | 96 runs | 192 runs | 384 runs | 768 runs | 1,536 runs |
|---|---|---|---|---|---|---|---|---|
| P1 coached placebo | 0.0562 | 0.0543 | 0.0362 | 0.0251 | 0.0176 | 0.0124 | 0.00873 | 0.00617 |
| P2 uncoached placebo | 1.7 | 1.64 | 1.1 | 0.759 | 0.532 | 0.375 | 0.264 | 0.187 |
| E1 effect index | 0.0359 | 0.0349 | 0.0232 | 0.0161 | 0.0113 | 0.00794 | 0.0056 | 0.00396 |

## Runs a study needs for an 80% chance of rejecting

Analytic, at 5%/3, in a balanced study. `simulated` gives the registered rule's own power (randomization p-values, Holm's correction across the three primaries) at that many runs, where that is at most the largest simulated size.

| primary | true effect | size | runs needed | simulated power there |
|---|---|---|---|---|
| P1 coached placebo | today's estimate | 0.0181 | 184 | 0.80 |
| P2 uncoached placebo | today's estimate | 0.496 | 224 | 0.81 |
| P2 uncoached placebo | a quarter of its mean without the block | 0.317 | 536 | 0.79 |
| P2 uncoached placebo | half of its mean without the block | 0.634 | 144 | 0.81 |
| P2 uncoached placebo | all of its mean without the block | 1.27 | 40 | 0.84 |
| E1 effect index | today's estimate | 0.0102 | 240 | 0.81 |
| E1 effect index | a quarter of its mean without the block | 0.0324 | 32 | 0.90 |
| E1 effect index | half of its mean without the block | 0.0648 | 16 | 1.00 |
| E1 effect index | all of its mean without the block | 0.13 | 16 | 1.00 |

## What the run counts rest on

- **They restate the p-values.** At today's estimate, the runs needed follow from how many standard errors that estimate lies from zero (P1 1.15, P2 1.04, E1 1.02), and they scale as one over the effect squared. If the true effect sat at an end of today's 95% interval, a balanced study would need the runs below (for P1 only a rough guide, since a shift models it badly; see the third point):

  | primary | 95% interval | runs needed at its lower end | at its upper end |
  |---|---|---|---|
  | P1 coached placebo | -0.0153 to 0.0514 | 256 | 32 |
  | P2 uncoached placebo | -0.516 to 1.51 | 208 | 32 |
  | E1 effect index | -0.0112 to 0.0316 | 200 | 32 |

- **The variances are noisy.** The two models' pooled variances rest on 7 (opus) and 8 (sol) degrees of freedom. A 95% range for their sum scales every run count above by 0.55 to 2.40 for P1; 0.55 to 2.40 for P2; 0.54 to 2.41 for E1.
- **A shift fits E1 better than P1 or P2.** The simulation adds the effect to every run in the block's arms. P1's jumps take the values the forecasters' rounding allows, each a multiple of 1/240 of a point, and 8 of 23 are zero, so this report gives P1 no shifted effects. P2's measure scores a step of D in a run's governor's-race answers by where it falls: D at 50%; -D/12 at 49.5% or 50.5%; -5D/12 at 49% or 51%; 0 at 48.5% or 51.5%. Its arm means move with how many runs put their step where, not as a shift.
- **E1's absolute jumps ignore each jump's direction,** so they score an answer the same whichever way round its forecaster entered it. Signed toward the direction most runs chose, E1 reads 0.0189 (standard error 0.0140, estimate over standard error 1.35) with the answers below as written and 0.0115 (standard error 0.0108, estimate over standard error 1.07) with them read the other way round, beside 0.0102 from the registered absolute jumps. Exploratory; the registered measure stands. The answers that run against their question's usual direction by at least 0.1 (a jump is Peltola's side minus Sullivan's), each with its run's own reasoning for it:
  - sol-06, sullivan_post: jump -0.22, a higher chance where Sullivan wins. Its reasoning: "Defeated senators do not routinely receive prompt executive appointments, but Sullivan's military and diplomatic experience makes him a credible candidate. His relationship with Trump and possible Cabinet turnover raise that prospect after defeat. Reelection substantially lowers the incentive to leave; the precise vote margin has little additional effect."

## A pooled second wave

The chance that today's runs pooled with a second wave cross 5%/3, if the true effect equals today's estimate, with the normal cutoff and then the t cutoff (today's degrees of freedom plus the wave's). Both standard errors come from estimated variances, so the normal cutoff rejects a true null more often than 5%/3. Neither counts the penalty a second look owes, so both overstate what an honest pooled test gives.

| primary | +24 runs | +48 runs | +96 runs | +192 runs | +384 runs | +768 runs | runs for 80% |
|---|---|---|---|---|---|---|---|
| P1 coached placebo | 0.15 / 0.11 | 0.33 / 0.30 | 0.61 / 0.59 | 0.89 / 0.88 | 0.99 / 0.99 | 1.00 / 1.00 | 152 / 160 |
| P2 uncoached placebo | 0.11 / 0.07 | 0.25 / 0.22 | 0.50 / 0.48 | 0.81 / 0.80 | 0.98 / 0.98 | 1.00 / 1.00 | 192 / 192 |
| E1 effect index | 0.10 / 0.07 | 0.23 / 0.20 | 0.47 / 0.45 | 0.78 / 0.77 | 0.97 / 0.97 | 1.00 / 1.00 | 208 / 208 |

## The rule's false rejections

With no effect anywhere, the share of 2,000 simulated studies in which Holm's correction rejected each primary, and any of them; the registered rule holds the last at or below 5%.

| runs | P1 coached placebo | P2 uncoached placebo | E1 effect index | any |
|---|---|---|---|---|
| 24 | 0.016 | 0.018 | 0.019 | 0.048 |
| 48 | 0.015 | 0.015 | 0.020 | 0.048 |
| 96 | 0.021 | 0.017 | 0.019 | 0.054 |
| 192 | 0.015 | 0.013 | 0.018 | 0.043 |
| 384 | 0.013 | 0.014 | 0.017 | 0.043 |
| 768 | 0.017 | 0.019 | 0.013 | 0.048 |

Simulation: 2,000 studies per cell, 2,000 re-assignments per study, seed 0.
