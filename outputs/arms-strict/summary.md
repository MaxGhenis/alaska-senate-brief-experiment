# What the briefs' blocks change

Model forecasts under stated scenarios, not observations. Repaired runs left out (--strict). Accepted runs by family and arm: opus 00: 0, opus 01: 0, opus 10: 0, opus 11: 0, sol 00: 3, sol 01: 2, sol 10: 3, sol 11: 2. Only sol has a run in every arm, so every estimate uses its runs alone.

Recovered from their transcripts (`i86 recover`): none. Answers `i86 check` repaired (left out): opus-01, opus-02, opus-04, opus-05, opus-06, opus-07, opus-08, opus-09, opus-10, opus-11, opus-12.

## Registered primary outcomes

| outcome | questions | measure | block | estimate | 95% t interval | randomization p | Holm p |
|---|---|---|---|---|---|---|---|
| P1 coached placebo | bm2_yes_share | abs_jump | placebo | -0.01875 | [-0.06853, 0.03103] | 0.4179 | 0.913 |
| P2 uncoached placebo | gov_dem_share | abs_jump | placebo | 0.6535 | [-1.37, 2.677] | 0.3043 | 0.913 |
| E1 effect index | recount+contest+court_order+winner_sworn+sullivan_post | abs_jump | estimand | 0.006626 | [-0.03869, 0.05194] | 0.68 | 0.913 |

## By family (exploratory)

| outcome | Claude Opus 5.5 | GPT-6.1 Sol |
|---|---|---|
| P1 coached placebo | undefined [undefined, undefined] | -0.01875 [-0.06853, 0.03103] |
| P2 uncoached placebo | undefined [undefined, undefined] | 0.6535 [-1.37, 2.677] |
| E1 effect index | undefined [undefined, undefined] | 0.006626 [-0.03869, 0.05194] |

Every other effect is in effects.csv (exploratory); arm means are in arm_means.csv.
