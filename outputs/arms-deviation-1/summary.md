# What the briefs' blocks change

Deviation 1 (prereg/README.md), not the registered analysis: it also pools sol-04, sol-08, whose session audits failed only on exec scripts a blinded reviewer cleared (outputs/flag-review/). Every other choice is the registered analysis's.

Model forecasts under stated scenarios, not observations. Accepted runs by family and arm: opus 00: 3, opus 01: 3, opus 10: 3, opus 11: 2, sol 00: 3, sol 01: 3, sol 10: 3, sol 11: 3.

Recovered from their transcripts (`i86 recover`): none. Answers `i86 check` repaired: opus-01, opus-02, opus-04, opus-05, opus-06, opus-07, opus-08, opus-09, opus-10, opus-11, opus-12.

## Registered primary outcomes

| outcome | questions | measure | block | estimate | 95% t interval | randomization p | Holm p |
|---|---|---|---|---|---|---|---|
| P1 coached placebo | bm2_yes_share | abs_jump | placebo | 0.01806 | [-0.01534, 0.05145] | 0.2358 | 0.7074 |
| P2 uncoached placebo | gov_dem_share | abs_jump | placebo | 0.4958 | [-0.5164, 1.508] | 0.3184 | 0.7074 |
| E1 effect index | recount+contest+court_order+winner_sworn+sullivan_post | abs_jump | estimand | 0.01019 | [-0.01118, 0.03156] | 0.2669 | 0.7074 |

## By family (exploratory)

| outcome | Claude Opus 5.5 | GPT-6.1 Sol |
|---|---|---|
| P1 coached placebo | 0.0375 [-0.0141, 0.0891] | -0.001389 [-0.05323, 0.05045] |
| P2 uncoached placebo | 0.1514 [-1.434, 1.736] | 0.8403 [-0.7103, 2.391] |
| E1 effect index | 0.01376 [-0.01785, 0.04538] | 0.006611 [-0.02784, 0.04106] |

Every other effect is in effects.csv (exploratory); arm means are in arm_means.csv.
