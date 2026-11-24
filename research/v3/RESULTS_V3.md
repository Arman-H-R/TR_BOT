# SMC XAUUSD research v3 — parameters, timeframes, multi-timeframe

*Status: COMPLETE (2026-11-24). There were 30 755 experiments: 30 752 ok, 2
errors (a config that produces no setups) and 1 documented skip (M1
optimisation). Every number comes from `reports/research_v3/experiments.sqlite`.
The generated tables are in `reports/research_v3/REPORT_TABLES.md` (`research/v3/s10_report.py`).
A copy of the report files (tables, CSVs, pre-registration, finalists) is in
`research/v3/reports/`. The experiment database (54 MB) stays local and git-ignored.*

`smc_core.py`, `backtest.py` and `live_trade.py` were **not modified** by this
research. The live demo bot kept running the shipped v2 rules throughout.

---

## 0. How to reproduce

```
python research/v3/check_v3.py        # parity + look-ahead checks
python research/v3/pipeline.py s2     # stages 2 -> 3 -> 4 -> 5 (each resumes)
python -u research/v3/s6_multiyear.py # multi-year optimisation
python research/v3/pipeline_tail.py   # waits, then stage 5 again, final test (s9), tables (s10)
```
Every experiment is one row in `experiments` and one row per data split in
`results` (`exp_id` = SHA-1 of the full spec → deduplication + resume). Failed
and skipped runs are stored with the traceback/reason. Optuna studies live in
`reports/research_v3/optuna/*.journal` (resume after a crash). The seeds are fixed
per stage: s2 = 11, s3 = 23, s4 = 37, s6 = 61/67, WF fold k = k.

## 1. Data and splits

| dataset | base candles | broker history | used for |
|---|---|---|---|
| A | M5 | 2025-06-23 .. 2026-11-18 | main research; every setup TF is resampled from M5, fills/exits on M5 |
| L | M15 | 2022-08-11 .. 2026-11-18 | multi-year (M15 / M30 / H1 setups), fills on M15 |
| H | H1 | real H1 only from 2017-08 | multi-year (H1 / H4 setups), fills on H1 |
| M1 | M1 | 2026-08-07 .. 2026-11-18 | diagnostic only (all of it lies in the test period) |

The broker keeps 100 000 candles per timeframe. There is no older M1/M5 data.
**Data defect:** the broker's H1 file holds about one candle a day before
mid-2017, so dataset H starts in 2017-08.

| split | period | role |
|---|---|---|
| train | 2025-07-01 .. 2026-01-01 | optimiser objective (dataset A) |
| ltrain / htrain | 2022-09 / 2017-08 .. 2026-01-01 | optimiser objective (multi-year) |
| validation | 2026-01-01 .. 2026-06-01 | candidate selection only |
| **test** | **2026-06-01 .. 2026-11-19** | **looked at once, in s9_final.py, after the finalists were frozen** |
| walk-forward | 7 folds: 4 months in-sample → next month, Nov 2025 .. May 2026 | inside train+val |
| last 12 months | 2025-11-19 .. 2026-11-19 | the user's reporting window (overlaps train/val/test) |

The selection of the shipped v2 rules saw Mar–Nov 2026 in the earlier research,
so for **v2 itself** the test split is not fully unseen. For every v3 candidate
it is.

**Costs (all stages):** spread 0.30 + slippage 0.05 on entries and stop exits +
5 USD/lot commission. Risk 1 % per trade, compounded. The reference simulator
(`backtest.run`) is used for the Config fields the fast engine does not model;
it charges the spread only.

## 2. Code audit (all 71 Config fields) — `research/v3/PARAM_AUDIT.md`

* **Dead parameters (verified):**
  * `entry_on_retouch` is read only on a code path nothing calls.
  * `bos_max_age` filters nothing; it only sizes the candle window.
  * `utc_start` and `utc_end` are only used when the time-zone data is missing.
* **No effect at the defaults:** `max_impulse_bars` and `min_pullback_bars`
  are always beaten by another rule. Switched off until another flag is on:
  `sl_use_wick`, `ob_max_body_ratio`, `trail_*`, `trend_ema`, `amd_*`,
  `lq_max_distance_pips`, `lq_sweep_within_bars` and `second_open_*`.
* **No look-ahead found** in swing confirmation, BOS (on a closed candle), the
  HTF trend or the AMD range.
* **Live vs backtest differences:**
  * Live sets `risk_percent` and `close_at_session_end` from the command line.
  * The H4 trend candles are only aligned because the broker clock is UTC+0.
  * Break-even: the backtest applies it on the candle range, live on a 5 s tick poll.
* **The "first touch decides the visit" rule is undocumented.** It applies in
  the backtest, live and the engine alike, so they agree.
* **The research engine treats `amd_mode="quality"` as off.** It was therefore
  tested in the reference simulator.
* **v3 evaluator checks** (`check_v3.py`):
  * 240/240 trades identical to the verified engine.
  * Candles resampled from M5 equal the broker's M15 candles.
  * Higher-timeframe context: changing the future leaves the past unchanged.
  * A value stamped at a candle close is not visible before that close.

## 3. Stage 1 — one parameter at a time (M5, 308 runs)

v2 baseline: train **+24.3 R** (87 trades), validation **+22.1 R** (69 trades).
About ±4 R is one trade, so differences under ~8 R are noise.

* **No effect at all:** `bos_max_age`, `good_quality`, `extra_trades_if_quality`,
  `trade_weekends`, `entry_on_retouch`, `utc_*`, `amd_min_range_pips` and
  `amd_quality`. Each was checked at every value tried.
* **Largest spread across values:** `rr`, `session_start`, `be_trigger_r`,
  `swing_lookback`, the order-block definition (`ob_use_body`,
  `ob_max_body_ratio`, `ob_pick`) and `cancel_on_opposite_break`.
* **Better than v2 in both train and validation:**
  * `swing_lookback` = 1
  * `ob_require_small_body` = True / `ob_max_body_ratio` = 0.7
  * `max_impulse_bars` = 3
  * `trend_filter` = H4 and `trend_ema` = 200
  * `lq_max_distance_pips` = 60
  * `sharp_impulse_bars` = 1
* **Hurts:** the AMD filter (9 trades, −7 R train). `sl_use_wick=False` with
  body OBs gives −3 R and −6 R.

## 4. Stage 2 — combined search on M5 (Optuna TPE, 2 × 3 000 trials)

| search objective | configs | share profitable in validation | corr(train R, val R) |
|---|---|---|---|
| t-statistic of R | 3 000 | 86 % (of 277 with enough trades) | 0.69 |
| total R (DD-capped) | 3 000 | 99 % | **0.26** |

The best robust configs, ranked by min(t_train, t_val), reach about +65 R in
train and +40..55 R in validation against v2's +24 / +22. They trade about half
as often. Two warnings:

1. **Train winners predict validation poorly** in the profit search (corr 0.26).
2. **Almost everything made money in validation (Jan–May 2026).** That makes it a
   weak filter, so walk-forward and the test split carry the weight.

## 5. Stage 3 — each timeframe on its own (setup TF re-optimised, fills on M5)

| setup TF | configs | best robust: train trades / R / t | val trades / R / t / PF | verdict |
|---|---|---|---|---|
| M1 | — | skipped: all M1 data is inside the test split | — | diagnostic in s9 |
| M5 | 6 000 | 49 / +67.6 / 2.83 | 27 / +55.1 / 2.82 / 4.38 | strongest |
| M15 | 1 500 | 74 / +56.2 / 4.80 | 60 / +29.7 / 2.60 / 2.06 | 2nd; train t drops by half in validation |
| M30 | 1 500 | 62 / +15.5 / 1.04 | 29 / +8.8 / 0.76 / 1.49 | no reliable edge |
| H1 | 1 500 | 38 / +18.0 / 2.70 | 31 / +10.9 / 1.72 / 2.55 | weak, small samples |
| H4 | 1 500 | — | — | cannot reach 30 trades in 6 months; see multi-year |

With the v2 defaults unchanged, every higher TF makes 0–28 trades and about 0 R.
The rules need re-tuning per timeframe.

## 6. Stage 4 — multi-timeframe architectures (4 064 enumerated + 4 × 1 200 searched)

What can be split across timeframes: context (structure / trend), liquidity, POI
(HTF order block), entry confirmation and execution. Swing, BOS, OB, impulse
and pullback stay on one TF: smc_core defines the OB as the last opposite candle
of the BOS impulse and counts impulse/pullback in those candles, so splitting
them is undefined.

**Effect of each HTF confirmation** (validation R vs the same setup without it, median):

| confirmation | mandatory ("all") | optional ("any") |
|---|---|---|
| EMA trend | −2.0 R | — |
| HTF BOS structure | −7.0 R | with EMA: 0.0 R |
| HTF liquidity sweep | — | with EMA: 0.0 / with BOS: −3.9 R |
| HTF order-block zone | **−12.8 R** (≈ 9 trades left) | with EMA: 0.0 R |

**Lower-TF entry confirmation** (setups above M5):

| entry rule | median validation trades | median validation R vs none |
|---|---|---|
| CHoCH | ≈ 4 | −16.5 R |
| close back out | ≈ 14 | −9.9 R |
| candle colour | ≈ 14 | −10.6 R |

**By number of timeframes:**

| timeframes | configs | best validation R | median validation R vs base |
|---|---|---|---|
| 1 | 3 | +55.1 | 0 |
| 2 | 309 | +59.2 | −9.6 |
| 3 | 2 976 | +53.3 | −9.9 |
| 4 | 776 | +16.9 | −19.4 |

Mandatory confirmations add no statistical value: they mostly remove trades.
Optional (any-of) context is roughly neutral. A 4-TF architecture is never
justified. The best MTF configs (an M15 setup with optional H1/H4 context and an
M5 entry) reached validation t ≈ 3.0, level with the best single-TF M5. **None of
them survived the test (section 9).**

## 7. Stage 6 — multi-year robustness

The optimiser trained only on data before 2026; validation and test are unchanged.

* **M15 setup, Sep 2022 .. Dec 2025** (1 000 trials):
  * Longer training generalises better: corr(multi-year R, validation R) = 0.54,
    against 0.26–0.31 with 6 months.
  * The edge is regime-dependent. Nearly all profit is in 2024–2025 (gold's
    strong trend); 2022–2023 is flat or negative for most top configs.
* **Per year, after the test** (`s9_long`, M15 fills, R):

| config | 2022 | 2023 | 2024 | 2025 | 2026 | test |
|---|---|---|---|---|---|---|
| multi-year L_M15 | −19.7 | +25.7 | +52.5 | +50.0 | +39.8 | −1.3 |
| multi-year L_H1 | +4.2 | +4.9 | +8.7 | +14.9 | +16.2 | +6.3 |
| multi-year L_M30 | −4.3 | +20.2 | −4.6 | +10.1 | +41.5 | +22.8 |
| best risk-adjusted (6-month optimised) | −0.1 | +11.2 | **−14.0** | +45.5 | +24.8 | −3.4 |
| robust contender #6 | +0.7 | −0.6 | **−29.3** | +49.3 | +1.6 | −19.4 |

* **H1 on Aug 2017 .. 2025 data:**
  * multi-year L_H1: 2017–19 +1.1, 2020–21 −6.0, 2022–23 +4.7, 2024–25 +20.8.
  * best single-TF H1: −21, −24, −25, +16. It only works in the recent trend.
* **H4:** no configuration reached the minimum sample (60 trades in 2017–2025
  and 10 in validation), so no H4 finalist exists. H4 is rejected for too few trades.

**Conclusion:** across 2017–2025 the SMC rules have no stable edge on H1/H4. On
M15 the edge appears mainly in trending 2024–2025. Configs tuned on 6 months lost
heavily in 2024 (−14 to −31 R): their validation success was regime-specific.

## 8. Stage 5 — walk-forward, stability, Monte Carlo (train + validation only)

**Walk-forward re-optimisation** (does *searching* work? Optuna on 4 months, the
winner traded the next month):

| OOS month | 2025-11 | 2025-12 | 2026-01 | 2026-02 | 2026-03 | 2026-04 | 2026-05 | total |
|---|---|---|---|---|---|---|---|---|
| tuned winner R | −6.7 | −6.9 | +4.6 | +11.9 | +8.9 | +1.9 | −7.8 | **+5.9** |
| top-10 mean R | −6.8 | −0.6 | −0.4 | +5.3 | +1.1 | +8.6 | −0.1 | +7.2 |

In-sample those winners made +20..+54 R per fold, so **re-optimising keeps ≈ 10 %.**

**Fixed walk-forward, stability, MC per finalist:** see `REPORT_TABLES.md`
("evidence per finalist"). Every leading candidate:
* was profitable in 7/7 walk-forward months and 5/5 two-month blocks;
* had 93–100 % of its ±1/±2-step neighbours profitable;
* had a Monte Carlo P(loss) of 0 % (bootstrap, block bootstrap and execution
  stress, train+val trades).

v2 scored worse on all of these (4/7 months, MC P(loss) 20 % under execution stress).

**Monte Carlo types** (5 000 runs each):
* *shuffle* = the same trades in a new order. It measures sequence risk only:
  DD and streak distributions.
* *bootstrap* = trades drawn with replacement. It measures the uncertainty of
  the result.
* *block10* = the bootstrap, keeping 10-trade clusters.
* *exec_stress* = −0.05 R per trade, with 10 % of the winners missed.

**What stage 5 could not detect:** every one of these tests resamples the *same
period*. Stability and Monte Carlo looked excellent and still said nothing about
the regime change in section 9.

## 9. FINAL TEST — 2026-06-01 .. 2026-11-19, evaluated once

The candidates and the decision rule were frozen first in
`reports/research_v3/PREREGISTRATION.md` (11:11). The test ran at 11:17.

| candidate (role) | train R | val R | test trades | **test R** | test PF | test win % | test max DD | test return |
|---|---|---|---|---|---|---|---|---|
| best risk-adjusted (pre-registered BEST OVERALL / best MTF) | +63.4 | +39.3 | 36 | **−6.0** | 0.74 | 28 % | 14.0 % | −6.3 % |
| robust contender #6 (pre-registered MOST ROBUST) | +50.0 | +26.2 | 60 | **−19.1** | 0.56 | 28 % | 18.9 % | −17.7 % |
| best single-TF M5 (pre-registered BEST SINGLE-TF) | +67.6 | +55.1 | 32 | **−1.0** | 0.96 | 16 % | 18.1 % | −2.0 % |
| best MTF #2 | +44.6 | +29.8 | 55 | −10.2 | 0.72 | 31 % | 15.4 % | −10.1 % |
| best single-TF M15 | +56.2 | +29.7 | 77 | −14.2 | 0.73 | 31 % | 21.2 % | −13.8 % |
| highest historical R (pre-registered HIGHEST PROFIT) | +118.4 | +49.2 | 56 | **+17.9** | 1.40 | 21 % | 12.9 % | +16.9 % |
| highest profit (val R) | +48.3 | +68.1 | 69 | +14.5 | 1.27 | 20 % | 24.6 % | +12.5 % |
| multi-year L_M30 | −0.2 | +18.6 | 40 | +22.7 | 2.26 | 55 % | 4.9 % | +24.9 % |
| multi-year L_H1 | +2.4 | +9.9 | 15 | +6.2 | 1.71 | 33 % | 3.0 % | +6.1 % |
| best single-TF H1 | +19.0 | +10.7 | 36 | −4.6 | 0.68 | 50 % | 5.6 % | −4.7 % |
| best single-TF M30 | +15.5 | +8.8 | 50 | +3.4 | 1.10 | 28 % | 13.3 % | +2.5 % |
| **v2 (shipped, live on demo)** | +24.3 | +22.1 | **82** | **+30.6** | **1.64** | 43 % | **8.6 %** | **+33.5 %** |
| v1 | −33.9 | +17.7 | 124 | +22.1 | 1.35 | 52 % | 9.2 % | +22.4 % |

* **Spearman(pre-registered score, test R) = −0.80.** The more a config was
  tuned towards Jul 2025 – May 2026, the worse it did from June 2026.
* **The optimised configs share one regime bet:**
  * they trade from 03:00 NY (London);
  * body OBs with wide stops (15–30 pips);
  * RR 5–7 with break-even off or late;
  * an H1/H4 trend filter.

  That worked in the trending first half of 2026. On test their win rate was 16–31 %.
* **v2 caveat:** v2's rules were chosen in the earlier research with Mar–Nov
  2026 in view, so its test is not fully out-of-sample. Still, no v3 candidate
  beat it on the unseen data.
* **Reference check:** the five M5 finalists give identical trades and R in the
  original `backtest.run` (`s9_refcheck.csv`).
* **M1 diagnostic** (2026-08-10 .. 11-19):
  * Running M5 setups with M1 fills gives the same result (v2: +26.5 R on M5
    fills vs +26.4 R on M1). Execution granularity is not hiding anything.
  * The same rules on **M1 candles lose** (v2 −31 R, PF 0.80; every candidate
    negative). M1 is not viable.

### All 71 parameters

The complete 71-parameter configuration of every finalist, next to the v2
value, is in `reports/research_v3/s9_params71.csv` and `REPORT_TABLES.md`. The
leading candidates change 22–25 of the 71 values. The rest stay at v2. The
changes are summarised in the "regime bet" bullets above.

### Last 12 months (2025-11-19 .. 2026-11-19), risk 1 %, month by month

These months overlap train and validation for the v3 candidates: their first 7
months are in-sample. Read the v3 numbers from June 2026 on.

**v2 (shipped):**
* Return +76.9 %, +60.7 R, PF 1.57, max DD 12.7 %.
* 173 trades (88 buy / 85 sell), 3.3 a week.
* 40 TP, 101 SL, 23 BE, 9 other; win rate 39.9 %.

| month | profit % | trades | buy | sell | trades/wk | TP | SL | BE | other | win % | R |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2025-11 | −1.23 | 6 | 3 | 3 | 1.4 | 1 | 5 | 0 | 0 | 16.7 | −1.1 |
| 2025-12 | +9.18 | 16 | 12 | 4 | 3.7 | 4 | 7 | 4 | 1 | 56.2 | +9.1 |
| 2026-01 | +17.78 | 15 | 6 | 9 | 3.4 | 6 | 7 | 2 | 0 | 53.3 | +16.9 |
| 2026-02 | −3.21 | 10 | 4 | 6 | 2.3 | 1 | 6 | 2 | 1 | 20.0 | −3.1 |
| 2026-03 | −6.82 | 10 | 9 | 1 | 2.3 | 0 | 7 | 3 | 0 | 30.0 | −7.0 |
| 2026-04 | +2.07 | 20 | 6 | 14 | 4.6 | 4 | 15 | 0 | 1 | 25.0 | +2.5 |
| 2026-05 | +13.29 | 14 | 7 | 7 | 3.2 | 5 | 8 | 0 | 1 | 42.9 | +12.9 |
| 2026-06 | +5.84 | 11 | 4 | 7 | 2.5 | 3 | 6 | 2 | 0 | 45.5 | +5.9 |
| 2026-07 | −0.02 | 13 | 5 | 8 | 3.0 | 2 | 8 | 2 | 1 | 38.5 | +0.2 |
| 2026-08 | −1.03 | 15 | 7 | 8 | 3.4 | 2 | 9 | 3 | 1 | 40.0 | −0.8 |
| 2026-09 | +11.56 | 16 | 11 | 5 | 3.7 | 5 | 10 | 0 | 1 | 37.5 | +11.4 |
| 2026-10 | +5.82 | 18 | 9 | 9 | 4.1 | 4 | 9 | 3 | 2 | 44.4 | +6.0 |
| 2026-11 | +7.98 | 9 | 5 | 4 | 2.1 | 3 | 4 | 2 | 0 | 55.6 | +7.9 |

**best risk-adjusted** (pre-registered best overall):
* +48.6 % over 12 months, but from June: +8.9, −3.0, −3.2, −5.0, −1.0, −2.6 %.
* 75 trades, 1.4 a week; win rate 44 %; max DD 14 %.

**highest historical R:**
* +140 % over 12 months (+94 R); from June: −2.4, +14.0, −0.4, −5.0, +9.3, +1.6 %.
* 121 trades, 2.3 a week; 26 TP / 89 SL; win rate 26 %; max DD 12.9 %.

Every candidate's monthly table, the 12-month summary (average DD, return/DD,
daily Sharpe) and the Monte Carlo tables are in `REPORT_TABLES.md`.

## 10. Ranking and recommendation

**Pre-registered decision rule:** recommend "best overall" unless its test is
negative; if it is negative, recommend no change.
→ Its test result is **−6.0 R**, so **no change to the live bot is recommended.**

Final ranking by robustness, consistency, profitability and risk, using all
evidence including the test:

1. **v2 (shipped)**:
   * Test +30.6 R (PF 1.64, DD 8.6 %); last 12 months +76.9 %, DD 12.7 %.
   * 8 of 13 calendar months positive; the most trades, so the largest sample.
   * Caveat: its test is not fully unseen. **Keep it.** The demo forward test
     is the only clean out-of-sample evidence left.
2. **highest historical R (M5)**:
   * Test +17.9 R: profitable but below v2.
   * Win rate only 21–26 %, so long losing streaks (MC p95 streak 18).
   * Not better than v2 → not recommended.
3. **multi-year L_M30 / L_H1**:
   * Positive on test (+22.7 / +6.2 R), but negative in some training years.
   * Few trades (L_H1: 15 on test).
   * Not selectable on the pre-test evidence. Worth re-checking after more
     forward data.
4. **Every 6-month-optimised and MTF candidate**: negative on test → **rejected**.
5. **M30 / H1 / H4 single-TF, M1**: no stable edge (H4 too few trades, M1 loses) → **rejected**.

**What failed and why:**
1. **Parameter optimisation on about 1 year of M5 data.** 6 000 + 6 000 + 4 800
   trials found configs 2–3× better in-sample.
   * Walk-forward showed that searching keeps ≈ 10 % out of sample, and the test
     reversed the ranking (−0.80).
   * The cause is regime fitting (London-session trend trading in H1 2026), not
     look-ahead: the parity, resampling and look-ahead checks all passed.
2. **MTF confirmations.** Mandatory ones cut trades and R. Optional ones are
   neutral; their validation gains did not survive the test.
3. **Higher timeframes.** Too few trades. H1/H4 show no stable edge over 2017–2025.
4. **Stability and Monte Carlo.** Necessary but not sufficient. They resample one
   regime and cannot see the next one.

**What would add real evidence** (not done; needs your decision):
* longer M5/M1 history (e.g. Dukascopy 2015–2025) to test v2 itself across regimes;
* keep the v2 demo forward test running without gaps, in a standalone
  PowerShell window, not inside this app.
