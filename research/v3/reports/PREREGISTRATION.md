# Pre-registered final candidates (written 2026-11-24 11:11:56 - BEFORE the test split was evaluated)

Evidence used: train (2025-07..12), validation (2026-01..05), walk-forward months
(2025-11..2026-05), two-month blocks, parameter stability (+/-1,2 steps), Monte
Carlo on train+val trades. Composite score in research/v3/s5_summary.py.
The test split (2026-06-01 .. 2026-11-19) has not been evaluated for any v3 candidate.

| role | finalist (finalists.json key) | why |
|---|---|---|
| Best overall | best risk-adjusted (M15 setup; optional M30 EMA / H4 sweep / D1 OB context; M5 candle-colour entry; M5 fills) | highest composite score: robust t 2.62, 7/7 WF months positive, 5/5 blocks, 97 % of neighbours profitable, MC P(loss) 0 % even under execution stress |
| Most robust | robust contender #6 (M15 setup; optional H4 BOS / H4 OB context; M5 fills) | highest robust t (2.97 train & val), 7/7 WF, 5/5 blocks, 97 % neighbours profitable |
| Best single-timeframe | best single-TF M5 (no context, no entry filter) | best composite score among pure single-TF configs (2.26): robust t 2.82, 7/7 WF, 100 % neighbours profitable |
| Best multi-timeframe | best risk-adjusted (see above); runner-up best MTF #2 (M15 + optional H1 EMA/sweep + M5 close-out entry) | |
| Highest profit | highest historical R (train+val) | +167 R train+val, but robust t only 2.03 and DD 8 % |
| Baseline | v2 baseline (shipped) | |

Decision rule fixed now: the recommendation is "Best overall" unless its test
result is negative, in which case NO change to the live bot is recommended
(the next candidates are not promoted on the basis of the test).

Correction at 2026-11-24 11:12:12 (still before the test): robust contender #6 uses H4 context, so it is multi-timeframe; the best pure single-TF candidate is best single-TF M5.
