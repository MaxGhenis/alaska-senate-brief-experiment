# What the briefs' blocks change

Model forecasts under stated scenarios, not observations. Accepted runs by family and arm: opus 00: 3, opus 01: 3, opus 10: 3, opus 11: 2, sol 00: 3, sol 01: 2, sol 10: 3, sol 11: 2.

Recovered from their transcripts (`i86 recover`): none. Answers `i86 check` repaired: opus-01, opus-02, opus-04, opus-05, opus-06, opus-07, opus-08, opus-09, opus-10, opus-11, opus-12.

## Registered primary outcomes

| outcome | questions | measure | block | estimate | 95% t interval | randomization p | Holm p |
|---|---|---|---|---|---|---|---|
| P1 coached placebo | bm2_yes_share | abs_jump | placebo | 0.009375 | [-0.02285, 0.0416] | 0.5302 | 0.9798 |
| P2 uncoached placebo | gov_dem_share | abs_jump | placebo | 0.4024 | [-0.7568, 1.562] | 0.4899 | 0.9798 |
| E1 effect index | recount+contest+court_order+winner_sworn+sullivan_post | abs_jump | estimand | 0.0102 | [-0.01487, 0.03526] | 0.3105 | 0.9316 |

## By family (exploratory)

| outcome | Claude Opus 5.5 | GPT-6.1 Sol |
|---|---|---|
| P1 coached placebo | 0.0375 [-0.0141, 0.0891] | -0.01875 [-0.06853, 0.03103] |
| P2 uncoached placebo | 0.1514 [-1.434, 1.736] | 0.6535 [-1.37, 2.677] |
| E1 effect index | 0.01376 [-0.01785, 0.04538] | 0.006626 [-0.03869, 0.05194] |

Every other effect is in effects.csv (exploratory); arm means are in arm_means.csv.
