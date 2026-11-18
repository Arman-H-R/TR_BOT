# v2.0.0 research report: XAUUSD M5 SMC bot

Date: 2026-11-18. All numbers are reproducible with the commands in section G.
Raw tables are in `reports/research/` (git-ignored; regenerate them with the scripts).

## Summary

| | v1.0.0 (shipped) | **v2.0.0 (final)** |
|---|---|---|
| Trades / week | 5.2 | 3.3 |
| Avg R per trade, in-sample (Jul 2025 – Feb 2026) | −0.108 | **+0.340** |
| Avg R per trade, out-of-sample (Mar – Nov 2026) | +0.137 | **+0.309** |
| Full 17 months, realistic costs | +5.9 R, +1.5 % | **+77.0 R, +45.1 %** |
| Profit factor | 1.03 | **1.51** |
| Max drawdown | 18.5 % (40.0 R) | **9.7 % (20.4 R)** |
| Sharpe / Sortino (daily, annualised) | 0.44 / 0.85 | **1.95 / 4.51** |
| Profitable months | 8 / 17 | **11 / 17** |
| Monte Carlo P(loss over 17 months) | 48 % | **0.9 %** |

These rows use realistic costs: spread 0.30, 0.05 slippage, 5 USD/lot commission, and gap fills. Risk is 0.5 % per trade.

**What changed** (three rules plus two backtest fixes; the entry logic is otherwise untouched):

1. **Flat by Friday 16:00 New York.** No new entries after that time, and any open position is closed. Weekend gaps went straight through stops.
2. **Close-out confirmation.** The candle that touches the order block must close back outside the zone. A buy needs the close above the OB top; a sell needs the close below the OB bottom.
3. **Break-even at +2R instead of +1R.** At 1R the stop moved too early and turned winners into break-evens.
4. **Backtest fix: gap fills.** A candle that opens beyond the stop now fills at that open. The old backtest overstated v1 by about 16 R over 17 months.
5. **Live-bot fix.** Each position's real risk is saved, so break-even and trailing survive restarts and `rr` changes.

**What the evidence does not support.** Big optimisation did not carry over:

- The best of 35,090 configurations made +0.96 R/trade in-sample but only **+0.02 R** out-of-sample.
- A walk-forward re-optimisation every 2 months earned less than leaving v1 alone.
- On 4 years of M15 history (2022–2026) this SMC logic **lost money in 2022–2024** under every variant, v2 included. It only made money in 2025–26.

Treat v2 as a better-controlled version of a strategy whose long-run edge is **not proven**. Forward-test it on demo.

---

## A. Changes implemented

| Area | v1.0.0 | v2.0.0 | Why |
|---|---|---|---|
| Friday | held over the weekend | `friday_close = 16:00` NY: no entries from then, positions closed | Weekend and daily-break gaps cost up to −5.2 R per trade. It improved both halves (IS −20.2 → −11.7 R, OOS +26.1 → +38.3 R). |
| Entry confirmation | any touch | `entry_confirm_close = True` | It was the strongest parameter in the in-sample search (median score −2.22 → −0.41) and also helped OOS. |
| Break-even | +1R | `be_trigger_r = 2.0` | Positive in both halves. 1.5–2R sits on a plateau, while no break-even and 3R are worse OOS. |
| TP | 4R | 4R (unchanged) | 4R and 5R tie OOS (+0.31 vs +0.30), so it was not changed. |
| Backtest stop fills | always at the stop price | at the open when the candle gaps through | Correctness. `gap_fill=False` gives the old behaviour. |
| Live position management | R = \|TP − entry\| / `Config.rr` | R saved per ticket (`reports/live_positions.json`), the order comment `smc r=…` as fallback, legacy formula last | Changing `rr` or adding a trail no longer mis-manages open positions after a restart. |
| Live trailing | none | `manage()` runs `core.apply_trailing` (off by default, `trail_steps`) | Same code as the backtest. The trail state is read from the current SL, so a restart is safe. |
| New options (off by default) | — | `trend_filter` ("H1"/"H4"), `trend_ema` | They helped on M5 but hurt on 4 years of M15, so they are not enabled. Live uses the broker's H1/H4 candles, and a test checks they equal the backtest's. |
| `--max-trades` CLI | default 3 overrode the config | default = `Config.max_trades_per_day` | Avoids silent overrides. |

## B. Experimental results

### Data and protocol

- **Data.** XAUUSD from the broker's MT5:
  - M5: 2025-06-23 to 2026-11-18 (99,999 candles, the terminal's maximum).
  - M15: 2022-08 to 2026-11 (regime check).
  - M1: last 3.5 months (not used).
- **Splits.**
  - In-sample (IS): 2025-07-01 to 2026-03-01. Every choice was made here.
  - Out-of-sample (OOS): 2026-03-01 to 2026-11-18.
  - Walk-forward: 6-month train, 2-month test, rolling and anchored.
- **Costs.** Fixed spread of 0.30, the live demo spread. MT5's per-candle spread is the *minimum* inside the candle (0.09–0.16 in 2025), so it would understate costs.
  - "real" adds 0.05 slippage on market entries and stop exits, plus 5 USD/lot commission.
  - "stress" uses 0.15 slippage and 7 USD/lot.
- **Engine.** `research/engine.py` reproduces `backtest.run()` trade for trade: 382/382 trades, identical R to 1e-16. It runs about 100× faster, because setups are found once per rule set and filters/exits are applied afterwards.
- **Honesty note.** The OOS half was *looked at* while choosing between a handful of simple candidates in stage 10. So it is not perfectly untouched. The only truly unseen test is the forward test.

### Stages (scripts in `research/`, outputs in `reports/research/`)

| Stage | What | Result |
|---|---|---|
| s0 | v1 baseline, IS/OOS, 3 cost levels | IS −0.108 R, OOS +0.137 R; full period with real costs +0.016 R/trade (≈ zero) |
| s1 | One-at-a-time sensitivity (≈100 variants) | Helped in both halves: H1/H4 trend filter, close-out confirmation, morning session, BE at 2R. Helped in one half only: sells only, AMD filter, limit entries, TP 8R. Hurt: all-day trading, rr ≤ 2.5. |
| s2/s3 | Random search, 290 structures × 121 filter/exit draws = **35,090 configs** | In-sample rank barely predicts OOS (Spearman 0.27). The IS #1 gave +0.96 IS and +0.02 OOS; the IS top 200 averaged +0.51 IS and +0.03 OOS. |
| s4 | **New strategies**, 1,570 variants: NY opening-range breakout, pre-NY sweep reversal, H1/H4-trend EMA pullback, Donchian breakout | Every family's IS winner decayed OOS. ORB: +0.58 → −0.15. Sweep: +0.30 → −0.30. Donchian: +0.41 → +0.02. Pullback: +0.27 → +0.02. |
| s5 | **Walk-forward** of the s3 selection, Jan–Nov 2026 | Rolling: +21.9 R on 115 trades. Anchored: +1.2 R on 130 trades. v1 on the same months: +39.8 R on 226 trades. **Re-optimising lost to doing nothing.** |
| s6/s7 | 4 years of M15 (regime check) | SMC v1 lost −43 R (2023: −39 R, 2024: −20 R). The trend filter did not help. The best alternative family on 4 years (Donchian 2h + H4) made +0.11 R/trade (t = 1.25). |
| s8 | Ladder: add robust changes one by one | Friday close, then close-out, then BE2R each improved both M5 halves. |
| s10 | 7 simple candidates by 2-month block | The final rule set is positive in 7/9 blocks with a worst block of −5.6 R. v1: 5/9 blocks, worst −23.8 R. |
| s9 | The 5 versions side by side, plus Monte Carlo | Table below. |

### Versions compared (identical candles, costs and engine; realistic costs)

| Version | Trades/wk | IS avg R | OOS avg R | Full R | PF | Max DD | Sharpe | t-stat | Max consec. losses | Months + |
|---|---|---|---|---|---|---|---|---|---|---|
| V1 baseline (v1.0.0) | 5.2 | −0.108 | +0.137 | +5.9 | 1.03 | 18.5 % | 0.44 | 0.17 | 10 | 8/17 |
| V2 v1 parameters re-tuned (best IS, no new rule types) | 3.3 | +0.065 | **−0.174** | −11.5 | 0.92 | 14.1 % | −0.19 | −0.44 | 11 | 8/17 |
| V3 best of the 35k search (new logic allowed) | 2.2 | +0.959 | **+0.022** | +81.3 | 1.67 | 8.9 % | 1.93 | 2.29 | 17 | 11/17 |
| V4 best new strategy (ORB 08:00 NY, 60 min, H4 trend, 2R) | 2.0 | +0.581 | **−0.151** | +29.2 | 1.33 | 10.3 % | 1.21 | 1.63 | 7 | 11/17 |
| **V5 final v2.0.0** | **3.3** | **+0.340** | **+0.309** | **+77.0** | **1.51** | **9.7 %** | **1.95** | **2.42** | 11 | **11/17** |

The other metrics:

- **Full period with stress costs:** V1 −10.3 R, V5 +71.8 R.
- **Sortino:** V1 0.85, V5 4.51.
- **Calmar:** V1 0.06, V5 3.15.
- **Win rate:** V1 43.5 %, V5 37.8 %. Fewer winners, but bigger ones. V5's average winner is about 2.2 R.

**Why V5 and not V3:**

- V3's full-period figure (+81 R) is almost entirely in-sample. It made +0.02 R/trade OOS, which is a curve fit.
- V5 has the same edge in both halves.
- V5 sits on a parameter plateau. TP 4–5R and BE 1.5–2R all give about +0.30 R OOS.
- V5 changes three rules, each with a mechanical reason.

### Monte Carlo (5,000 bootstrap resamples of the full-period trades, 0.5 % risk)

| Version | Return p5 / p50 / p95 | P(loss) | Max DD p50 / p95 / p99 | Losing streak p95 |
|---|---|---|---|---|
| V1 | −23.4 / +0.6 / +35.3 % | 48 % | 16.5 / 30.0 / 35.7 % | 13 |
| V5 | +11.4 / +45.3 / +87.9 % | 0.9 % | 7.8 / 13.7 / 16.9 % | 15 |

Bootstrapping assumes the 2025–26 trade distribution continues. It does not cover a regime like 2023–24 on M15.

### V5 by month (realistic costs)

| month | trades | buy/sell | win % | R | % | TP | SL | BE | other* |
|---|---|---|---|---|---|---|---|---|---|
| 2025-07 | 18 | 10/8 | 28 | −1.3 | −0.7 | 3 | 13 | 1 | 1 |
| 2025-08 | 10 | 6/4 | 80 | +22.2 | +11.6 | 6 | 2 | 1 | 1 |
| 2025-09 | 15 | 9/6 | 33 | +9.7 | +4.9 | 5 | 10 | 0 | 0 |
| 2025-10 | 12 | 6/6 | 17 | −5.2 | −2.6 | 1 | 9 | 1 | 1 |
| 2025-11 | 16 | 10/6 | 13 | −10.3 | −5.0 | 1 | 14 | 1 | 0 |
| 2025-12 | 16 | 12/4 | 56 | +9.1 | +4.6 | 4 | 7 | 4 | 1 |
| 2026-01 | 15 | 6/9 | 53 | +16.9 | +8.7 | 6 | 7 | 2 | 0 |
| 2026-02 | 10 | 4/6 | 20 | −3.2 | −1.6 | 1 | 6 | 2 | 1 |
| 2026-03 | 10 | 9/1 | 30 | −7.0 | −3.5 | 0 | 7 | 3 | 0 |
| 2026-04 | 20 | 6/14 | 25 | +2.5 | +1.1 | 4 | 15 | 0 | 1 |
| 2026-05 | 14 | 7/7 | 43 | +12.9 | +6.6 | 5 | 8 | 0 | 1 |
| 2026-06 | 11 | 4/7 | 45 | +5.9 | +3.0 | 3 | 6 | 2 | 0 |
| 2026-07 | 13 | 5/8 | 38 | +0.2 | +0.0 | 2 | 8 | 2 | 1 |
| 2026-08 | 15 | 7/8 | 40 | −0.8 | −0.5 | 2 | 9 | 3 | 1 |
| 2026-09 | 16 | 11/5 | 38 | +11.4 | +5.7 | 5 | 10 | 0 | 1 |
| 2026-10 | 18 | 9/9 | 44 | +6.0 | +3.0 | 4 | 9 | 3 | 2 |
| 2026-11 (to 18th) | 9 | 5/4 | 56 | +7.9 | +4.0 | 3 | 4 | 2 | 0 |

\*other = Friday-close exits.

### Live = backtest checks (v2 rules)

| Check | Result |
|---|---|
| `research/check_engine.py` engine vs `backtest.py` | v2 default 240/240 trades identical, v1 + gap fills 382/382, max dR 1e-15 |
| `tests/test_live_parity.py --bars 20000` live loop vs `backtest.run()` | 55/55 trades, 64/64 signals; with 685 random stalls + 48 restarts the signals are identical, 1 trade missed on a candle the bot was down for |
| `tests/test_rules.py` | 6/6 pass (Friday rule, trend no look-ahead + live=backtest, state recovery, rr change, trail after restart, gap fill, Friday close) |

## C. Best final strategy

V5 is shipped as v2.0.0 and is the `smc_core.Config()` default. Its performance, kept apart by kind of evidence:

- **Historical (backtest, 17 months):** +77 R, PF 1.51, DD 9.7 %.
- **Out-of-sample (Mar–Nov 2026):** +0.309 R/trade over 126 trades, PF 1.49, DD 4.9 %. This half was looked at when choosing between simple candidates; see the honesty note.
- **Forward test:** none yet. Start it on demo (section G).

## D. Weaknesses and risks

- **Regime dependence (the biggest risk).** On 4 years of M15 the same SMC logic lost in 2022–2024. M5 history before June 2025 is not available from this broker, so this cannot be checked on the traded timeframe. If gold leaves the 2025–26 regime, the edge may disappear.
- **Small sample.** 238 trades. The t-statistic is 2.4, so the edge is about 2.4 standard errors from zero before accounting for the search.
- **Selection bias.** Many variants were tried. The final rules were chosen from robust *families*, not the single best point, but some optimism remains.
- **Win rate 38 %.** Streaks of 11 losses happened in the backtest, and the Monte Carlo p95 streak is 15. At 0.5 % risk that is about a 7–8 % drawdown.
- **Fewer trades.** 3.3 per week, down from 5.2. The earlier ≥ 4/week wish is not met. `entry_confirm_close=False` gives about 5/week (candidate E: +0.25 R/trade, DD 13.9 %).
- **Execution.**
  - Fills assume market at the candle close plus a fixed 0.30 spread.
  - The live bot reacts to a new candle within one poll (5 s).
  - Slippage beyond 0.05, or wider news spreads, reduce the edge. With stress costs it stays positive (+71.8 R).
- **Intrabar order.** A candle that hits both stop and target counts as a loss. That is conservative.
- **Not adopted:**
  - The H1/H4 trend filter: strong on M5, negative on M15 4 years.
  - Sells-only, the AMD filter, limit entries at the OB edge, ATR trailing, time stops, and the step trail. Each was inconsistent between halves.
  - Every alternative strategy.

## E. Final configuration (`smc_core.Config()` defaults)

Changed from v1: `be_trigger_r 1.0 → 2.0`, `entry_confirm_close False → True`, `friday_close None → 16:00`.

Everything else is as v1:

- **Instrument and session.** symbol XAUUSD, M5, pip 0.10, session 08:00–17:00 America/New_York, weekdays only.
- **Structure.** `swing_lookback=2`, `bos_max_age=80`, `ob_search_bars=20`, `ob_pick="last"`, `ob_use_body=False`, `ob_valid_bars=80`.
- **Pullback.** `min_pullback_bars=3`, `pullback_extra_bars=2`, `entry_on_retouch=True`, `fail_on_touch=False`.
- **Liquidity.** `lq_tolerance_pips=10`, `lq_min_touches=2`, `lq_lookback_bars=80`, `lq_require_between_bos=True`, `lq_allow_pre_ob=False`.
- **Risk.** `sl_buffer_pips=4`, `rr=4.0`, `be_cost_pips=2`, `min_sl_pips=4`, `max_sl_pips=150`, `risk_percent=0.5`.
- **Quality.** Weights 2/2/1/1/2, `min_quality=1`.
- **Trade limits.** `max_trades_per_day=3` (+2 if quality ≥ 5).
- **Left off.** `trend_filter="off"`, `trail_steps=False`, `amd_mode="off"`, `close_at_session_end=False`.

To run v1 exactly:

```
--set be_trigger_r=1 --set entry_confirm_close=false --set friday_close=none
```

`friday_close=none` works on the backtest command line. In code, set `cfg.friday_close = None`.

## F. Files

| File | Status | Purpose |
|---|---|---|
| `smc_core.py` | modified | New config fields (Friday close, close-out confirmation, trend filter, v2 defaults), `friday_cutoff()`, `trend_series()` / `trend_from_htf()`, trend + confirmation in `SetupTracker.update()` |
| `backtest.py` | modified | Gap fills, Friday close and entry block, trend input |
| `live_trade.py` | modified | `PositionState` (saved risk), `manage()` with BE + trail through `trade_from_position()`, Friday flatten, broker H1/H4 trend, `--state`, `--max-trades` default |
| `tests/test_rules.py` | new | Friday rule, trend parity and no look-ahead, state recovery, rr change, trailing after a restart, gap fill and Friday close inside `backtest.run()` |
| `tests/test_live_parity.py` | modified | Feeds the trend, applies the Friday gate |
| `research/engine.py` | new | Fast two-stage backtester, exact parity with `backtest.run()` |
| `research/metrics.py` | new | Sharpe, Sortino, Calmar, drawdown, streaks, monthly table, Monte Carlo |
| `research/cache.py`, `common.py`, `v2.py`, `final.py` | new | Candidate cache, periods/costs/score, Config → engine mapping, final config |
| `research/alt_strategies.py` | new | ORB, sweep, pullback, Donchian |
| `research/s0`…`s10_*.py`, `s3_analyze.py`, `check_engine.py`, `ref_backtest.py` | new | Every experiment in this report |
| `research/RESULTS.md` | new | This report |
| `VERSION`, `CHANGELOG.md`, `README.md`, `.gitignore` | modified | 2.0.0 |

v1.0.0 is preserved as git tag `v1.0.0` and branch `v1-baseline`.

## G. Commands

Run these from the project folder. MT5 must be open for the commands that load data.

```bash
# data snapshot used by the research (data/*.csv, git-ignored)
python research/dump_data.py

# backtest the final strategy (17 months, from MT5)
python backtest.py --mt5 --balance 4700 --out reports/v2

# the old v1 rules, for comparison
python backtest.py --mt5 --balance 4700 --set be_trigger_r=1 --set entry_confirm_close=false --set friday_close=none

# reproduce the research, in order (stage 3 is ~15-80 min on 15 cores)
cd research
python s0_baseline.py
python s1_sensitivity.py
python s2_build_structures.py 240
python s3_search.py 120
python s3_analyze.py
python s4_alternatives.py
python s5_walkforward.py
python s6_m15_history.py
python s7_alt_m15.py
python s8_ladder.py
python s10_candidates.py
python s9_compare.py
cd ..

# automated tests
python -m pytest tests/test_rules.py -q
python tests/test_live_parity.py --bars 65000   # live loop == backtest
python research/check_engine.py                 # research engine == backtest.py

# forward test: watch only, then trade the DEMO account
python live_trade.py --dry-run
python live_trade.py                            # only on the demo account
```

**Forward-test plan.** Run on demo for at least 8–12 weeks, or about 40 trades. `reports/forward_trades.csv` collects them. Compare the forward avg R with the backtest's +0.31 R and its spread. Stop and review if:

- the drawdown exceeds 15 %, or
- after 40 trades the average is below −0.1 R.

## H. Limitations and unfinished work

- **Older M5 history.** It is not available from the broker (the MT5 terminal is capped at 100k bars). A longer M5 test would need, for example, Dukascopy tick data from 2020–2025. That is a download, so I have not done it; it would be the most valuable next check.
- **Bayesian optimisation.** I used random search plus one-at-a-time sensitivity instead of Optuna. The walk-forward showed that more aggressive optimisation does not generalise on this data, so a smarter optimiser would mostly find sharper over-fits.
- **No forward test yet.**
