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

Analytic, at 5%/3, in a balanced study. `simulated` gives the registered rule's own power (randomization p-values, Holm's correction across the three primaries) at that many runs, where that is at most the largest simulated size.

| primary | true effect | size | runs needed | simulated power there |
|---|---|---|---|---|
| P1 coached placebo | today's estimate | 0.00938 | 544 | 0.79 |
| P2 uncoached placebo | today's estimate | 0.402 | 376 | 0.79 |
| P2 uncoached placebo | a quarter of its mean without the block | 0.317 | 600 | 0.80 |
| P2 uncoached placebo | half of its mean without the block | 0.634 | 152 | 0.80 |
| P2 uncoached placebo | all of its mean without the block | 1.27 | 48 | 0.88 |
| E1 effect index | today's estimate | 0.0102 | 272 | 0.81 |
| E1 effect index | a quarter of its mean without the block | 0.0328 | 32 | 0.85 |
| E1 effect index | half of its mean without the block | 0.0656 | 16 | 0.99 |
| E1 effect index | all of its mean without the block | 0.131 | 16 | 1.00 |

## What the run counts rest on

- **They restate the p-values.** At today's estimate, the runs needed follow from how many standard errors that estimate lies from zero (P1 0.63, P2 0.76, E1 0.89), and they scale as one over the effect squared. If the true effect sat at an end of today's 95% interval, a balanced study would need the runs below (for P1 only a rough guide, since a shift models it badly; see the third point):

  | primary | 95% interval | runs needed at its lower end | at its upper end |
  |---|---|---|---|
  | P1 coached placebo | -0.0229 to 0.0416 | 96 | 32 |
  | P2 uncoached placebo | -0.757 to 1.56 | 112 | 32 |
  | E1 effect index | -0.0149 to 0.0353 | 128 | 32 |

- **The variances are noisy.** The two models' pooled variances rest on 7 (opus) and 6 (sol) degrees of freedom. A 95% range for their sum scales every run count above by 0.53 to 2.60 for P1; 0.52 to 2.68 for P2; 0.51 to 2.78 for E1.
- **A shift fits E1 better than P1 or P2.** The simulation adds the effect to every run in the block's arms. P1's jumps take the values the forecasters' rounding allows, each a multiple of 1/240 of a point, and 8 of 21 are zero, so this report gives P1 no shifted effects. P2's measure scores a step of D in a run's governor's-race answers by where it falls: D at 50%; -D/12 at 49.5% or 50.5%; -5D/12 at 49% or 51%; 0 at 48.5% or 51.5%. Its arm means move with how many runs put their step where, not as a shift.
- **E1's absolute jumps fold reversed answers in.** sol-06 answered sullivan_post against the other runs' direction (jump -0.22). With each question's jump signed toward the direction most runs chose, E1 reads 0.0226 (standard error 0.0165, estimate over standard error 1.36) in place of 0.0102. Exploratory; the registered measure stands.

## A pooled second wave

The chance that today's runs pooled with a second wave cross 5%/3, if the true effect equals today's estimate, with the normal cutoff and then the t cutoff (today's degrees of freedom plus the wave's). Both standard errors come from estimated variances, so the normal cutoff rejects a true null more often than 5%/3. Neither counts the penalty a second look owes, so both overstate what an honest pooled test gives.

| primary | +24 runs | +48 runs | +96 runs | +192 runs | +384 runs | +768 runs | runs for 80% |
|---|---|---|---|---|---|---|---|
| P1 coached placebo | 0.02 / 0.01 | 0.07 / 0.06 | 0.16 / 0.15 | 0.35 / 0.34 | 0.66 / 0.66 | 0.94 / 0.94 | 520 / 520 |
| P2 uncoached placebo | 0.04 / 0.03 | 0.12 / 0.10 | 0.26 / 0.25 | 0.53 / 0.52 | 0.85 / 0.85 | 0.99 / 0.99 | 344 / 352 |
| E1 effect index | 0.07 / 0.05 | 0.19 / 0.16 | 0.40 / 0.38 | 0.71 / 0.70 | 0.95 / 0.95 | 1.00 / 1.00 | 240 / 240 |

## The rule's false rejections

With no effect anywhere, the share of 2,000 simulated studies in which Holm's correction rejected each primary, and any of them; the registered rule holds the last at or below 5%.

| runs | P1 coached placebo | P2 uncoached placebo | E1 effect index | any |
|---|---|---|---|---|
| 24 | 0.018 | 0.015 | 0.015 | 0.045 |
| 48 | 0.015 | 0.018 | 0.015 | 0.045 |
| 96 | 0.016 | 0.016 | 0.019 | 0.050 |
| 192 | 0.017 | 0.014 | 0.014 | 0.042 |
| 384 | 0.017 | 0.015 | 0.013 | 0.041 |
| 768 | 0.013 | 0.011 | 0.013 | 0.036 |

Simulation: 2,000 studies per cell, 2,000 re-assignments per study, seed 0.
