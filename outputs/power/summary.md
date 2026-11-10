# What more runs could change

Exploratory and post hoc: a planning calculation from `outputs/arms/per_run.csv` by `scripts/power.py`, not part of the registered analysis. These are model forecasts under stated scenarios, not observations.

## Today's estimates

| primary | runs | estimate | standard error | mean without the block | randomization p | Holm p |
|---|---|---|---|---|---|---|
| P1 coached placebo | 21 | 0.00938 | 0.0149 | 0.0222 | 0.530 | 0.980 |
| P2 uncoached placebo | 21 | 0.402 | 0.532 | 1.27 | 0.490 | 0.980 |
| E1 effect index | 21 | 0.0102 | 0.0114 | 0.131 | 0.311 | 0.932 |

## The smallest effect a study detects 80% of the time

Analytic, at 5%/3, the level Holm's correction applies to the smallest of three p-values. The first column uses today's standard error and degrees of freedom; the others, balanced studies.

| primary | today's runs | 24 runs | 48 runs | 96 runs | 192 runs | 384 runs | 768 runs | 1,536 runs |
|---|---|---|---|---|---|---|---|---|
| P1 coached placebo | 0.0544 | 0.049 | 0.0326 | 0.0226 | 0.0158 | 0.0112 | 0.00787 | 0.00556 |
| P2 uncoached placebo | 1.96 | 1.74 | 1.16 | 0.8 | 0.561 | 0.395 | 0.279 | 0.197 |
| E1 effect index | 0.0425 | 0.0373 | 0.0248 | 0.0171 | 0.012 | 0.00845 | 0.00596 | 0.00421 |

## Runs a study needs for an 80% chance of rejecting

Analytic, at 5%/3, in a balanced study, with the other two primaries' effects at zero. `simulated` gives the registered rule's own power (randomization p-values, Holm's correction across the three primaries) at that many runs under the same condition, where that is at most the largest simulated size. The search starts at 16 runs, two per model and arm.

| primary | true effect | size | runs needed | simulated power there |
|---|---|---|---|---|
| P1 coached placebo | today's estimate | 0.00938 | 544 | 0.79 |
| P2 uncoached placebo | today's estimate | 0.402 | 376 | 0.79 |
| P2 uncoached placebo | a quarter of its mean without the block | 0.317 | 600 | 0.80 |
| P2 uncoached placebo | half of its mean without the block | 0.634 | 152 | 0.80 |
| P2 uncoached placebo | all of its mean without the block | 1.27 | 48 | 0.88 |
| E1 effect index | today's estimate | 0.0102 | 272 | 0.81 |
| E1 effect index | a quarter of its mean without the block | 0.0328 | 32 | 0.85 |
| E1 effect index | half of its mean without the block | 0.0656 | 16 or fewer | 0.99 |
| E1 effect index | all of its mean without the block | 0.131 | 16 or fewer | 1.00 |

If all three true effects equal today's estimates at once, a study needs fewer runs: once Holm's correction rejects one primary, it tests the next at 5%/2 and the last at 5%. The simulated rule then reaches 80% at about 416 runs for P1, 304 runs for P2 and 240 runs for E1. At the runs in the table above, it rejects P1 91%, P2 89% and E1 87% of the time (`joint.csv`). Each simulated count carries Monte Carlo error of a few steps of 8 runs.

## What the run counts rest on

- **They restate the p-values.** At today's estimate, the runs needed follow from how many standard errors that estimate lies from zero (P1 0.63, P2 0.76, E1 0.89), and they scale as one over the effect squared. If the true effect sat at an end of today's 95% interval, a balanced study would need the runs below (for P1 only a rough guide, since a shift models it badly; see the third point). An end that would push a mean absolute jump below zero reads "not possible":

  | primary | 95% interval | runs needed at its lower end | at its upper end |
  |---|---|---|---|
  | P1 coached placebo | -0.0229 to 0.0416 | not possible | 32 |
  | P2 uncoached placebo | -0.757 to 1.56 | 112 | 32 |
  | E1 effect index | -0.0149 to 0.0353 | 128 | 32 |

- **The variances are noisy.** The two models' pooled variances rest on 7 (opus) and 6 (sol) degrees of freedom. A 95% range for their sum scales every run count above by 0.53 to 2.60 for P1; 0.52 to 2.68 for P2; 0.51 to 2.78 for E1.
- **A shift fits E1 better than P1 or P2.** The simulation adds the effect to every run in the block's arms. P1's jumps take the values the forecasters' rounding allows, each a multiple of 1/240 of a point, and 8 of 21 are zero, so this report gives P1 no shifted effect beyond today's estimate, and its simulated power there serves only as a rough guide. P2's measure scores a step of D in a run's governor's-race answers by where it falls: D at 50%; -D/12 at 49.5% or 50.5%; -5D/12 at 49% or 51%; 0 at 48.5% or 51.5%. Its arm means move with how many runs put their step where, not as a shift.
- **E1's absolute jumps ignore each jump's direction,** so they score an answer the same whichever way round its forecaster entered it. Signed toward the direction most runs chose, with the answers below read the other way round, E1 reads 0.0116 (standard error 0.0121, estimate over standard error 0.96), beside 0.0102 from the registered absolute jumps. With those answers as written, it reads 0.0226 (standard error 0.0165, estimate over standard error 1.36), which counts each one as a forecast against the other runs. Exploratory; the registered measure stands. The answers that run against their question's usual direction by at least 0.1 (a jump is Peltola's side minus Sullivan's), each with its run's own reasoning for it:
  - sol-06, sullivan_post: jump -0.22, a higher chance where Sullivan wins. Its reasoning: "Defeated senators do not routinely receive prompt executive appointments, but Sullivan's military and diplomatic experience makes him a credible candidate. His relationship with Trump and possible Cabinet turnover raise that prospect after defeat. Reelection substantially lowers the incentive to leave; the precise vote margin has little additional effect." Its sealed session log shows a slip: a sub-agent advised 0.25 to 0.30 after a defeat and 0.04 to 0.07 after reelection, and the run then entered 0.28 in the four ranges where Sullivan wins (`prereg/README.md`).

## A pooled second wave

The chance that today's runs pooled with a second wave cross 5%/3, if the true effect equals today's estimate, with the normal cutoff and then the t cutoff (today's degrees of freedom plus the wave's, a few fewer than one pooled analysis of all the runs would have). Both standard errors come from estimated variances, so the normal cutoff rejects a true null more often than 5%/3. Neither counts the penalty a second look owes, so both overstate what an honest pooled test gives.

| primary | +24 runs | +48 runs | +96 runs | +192 runs | +384 runs | +768 runs | runs for 80% |
|---|---|---|---|---|---|---|---|
| P1 coached placebo | 0.02 / 0.01 | 0.07 / 0.06 | 0.16 / 0.15 | 0.35 / 0.34 | 0.66 / 0.66 | 0.94 / 0.94 | 520 / 520 |
| P2 uncoached placebo | 0.04 / 0.03 | 0.12 / 0.10 | 0.26 / 0.25 | 0.53 / 0.52 | 0.85 / 0.85 | 0.99 / 0.99 | 344 / 352 |
| E1 effect index | 0.07 / 0.05 | 0.19 / 0.16 | 0.40 / 0.38 | 0.71 / 0.70 | 0.95 / 0.95 | 1.00 / 1.00 | 240 / 240 |

## The rule's false rejections

With no effect anywhere, the share of 2,000 simulated studies in which Holm's correction rejected each primary, and any of them. The registered rule holds the last at or below 5%, and a simulated share can stray from it by Monte Carlo error, about 0.5 points here.

| runs | P1 coached placebo | P2 uncoached placebo | E1 effect index | any |
|---|---|---|---|---|
| 24 | 0.018 | 0.015 | 0.015 | 0.045 |
| 48 | 0.015 | 0.018 | 0.015 | 0.045 |
| 96 | 0.016 | 0.016 | 0.019 | 0.050 |
| 192 | 0.017 | 0.014 | 0.014 | 0.042 |
| 384 | 0.017 | 0.015 | 0.013 | 0.041 |
| 768 | 0.013 | 0.011 | 0.013 | 0.036 |

Simulation: 2,000 studies per cell, 2,000 re-assignments per study, seed 0.
