# Changelog

Every push to GitHub gets a new version: the number is in `VERSION`, the same
number is a git tag (`v1.0.0`, ...), and a short note goes here.

`MAJOR.MINOR.PATCH` - PATCH = a fix, MINOR = a new option or feature,
MAJOR = a change that alters how the bot trades by default.

## v2.4.0 - 2026-11-24

- **New - research v3** (`research/v3/`, report `research/v3/RESULTS_V3.md`,
  tables in `research/v3/reports/`). No trading rule changed: the bot still
  trades v2.
  - **Scope:** 30 755 logged experiments.
    - all 71 Config parameters, one at a time;
    - Optuna searches on M5;
    - M15 / M30 / H1 / H4 re-optimised separately;
    - 2-, 3- and 4-timeframe architectures (HTF trend / BOS / sweep / OB
      confirmations, lower-TF entry confirmation);
    - multi-year runs on M15 (2022+) and H1 (2017+);
    - walk-forward, parameter stability, Monte Carlo;
    - one pre-registered test on Jun-Nov 2026.
  - **Result: nothing beat v2 out of sample.** The pre-registered best candidate
    made -6 R on the test, against +30.6 R for v2. Optimised configs fitted the
    H1-2026 regime: the rank correlation between pre-test score and test R was
    -0.80. M5 remains the best timeframe. Mandatory multi-timeframe
    confirmations cost trades and R. M1 candles lose.
  - **Audit** (`research/v3/PARAM_AUDIT.md`): `entry_on_retouch` and
    `bos_max_age` have no effect, and 9 more parameters are inert at the
    defaults. No look-ahead found.
- `research/engine.py`: session windows that cross midnight (Asia) are supported.
- `research/s11_sessions.py`: the session comparison (NY 13-22 UTC best).

## v2.3.0 - 2026-11-19

- **New option - `monthly_report.py --risk`**: the monthly table at any risk per
  trade. Last 12 months, same 174 trades: 0.5 % -> +29.6 %, max DD 5.4 %;
  1 % -> +68.7 %, max DD 11.1 %. Monte Carlo 95th-percentile drawdown 12 % vs
  23 %. The live default stays 0.5 % (`--risk 1` to change it).

## v2.2.0 - 2026-11-19

- **New - `monthly_report.py`**: the per-month table (profit %, buys/sells,
  trades per week, full TP / SL / break-even, win rate, R, profit factor,
  drawdown, balance) for the backtest (`python monthly_report.py`) or the demo
  forward test (`python monthly_report.py --forward`). v2 backtest, last 12
  months: +29.6 %, 174 trades, 3.3/week, 39 TP / 102 SL / 24 BE, max DD 5.4 %.
- Demo forward test of v2 started 2026-11-19 (`--demo-only`).

## v2.1.0 - 2026-11-19

- **New option - `--demo-only`**: the live bot refuses to start unless the MT5
  terminal is logged into a DEMO account. Used for the forward test that starts
  today (`python live_trade.py --demo-only`).

## v2.0.1 - 2026-11-19

- **Fix - server clock at start-up**: the live bot only trusts the broker's clock
  once the quote is actually moving. On the first v2 dry run a one-hour-old quote
  (terminal still syncing) made a UTC+0 server look like UTC-1, which would have
  shifted the NY session and the Friday cut-off by an hour. Without a live quote
  within 60 s it falls back to UTC+0 with a warning (`--server-offset` overrides).
  New test in `tests/test_rules.py`. Dry run on the demo account afterwards: clock
  UTC+0, every candle handled within 2 s, no errors, no orders.

## v2.0.0 - 2026-11-18

Default trading behaviour changes (hence MAJOR). Full study: `research/RESULTS.md`.

- **New default - Friday close**: no new trade from Friday 16:00 New York, open
  positions are closed then (`friday_close`). Weekend gaps went through stops.
- **New default - close-out confirmation**: the OB touch candle must close back
  outside the zone (`entry_confirm_close`).
- **New default - break-even at +2R** (was +1R, `be_trigger_r`).
- **Fix - backtest gap fills**: a candle opening beyond the stop now fills at its
  open (v1 results were ~16 R too optimistic over 17 months). `gap_fill=False`
  restores the old way.
- **Fix - live position management**: the risk of each position is saved
  (state file + order comment), so break-even/trailing survive restarts and `rr`
  changes; the live bot can now run the step trail (`trail_steps`, still off).
- **New option** (off): higher-timeframe trend filter (`trend_filter` H1/H4),
  live bot reads the broker's own H1/H4 candles.
- Result (17 months, realistic costs): v1 +5.9 R, DD 18.5 % -> v2 +77 R, DD 9.7 %,
  positive in-sample and out-of-sample. Caveat: the logic lost on 2022-24 M15.
- Research toolkit in `research/`, new tests `tests/test_rules.py`.
- v1.0.0 stays available: tag `v1.0.0`, branch `v1-baseline`.

## v1.0.0 - 2026-11-18

First version on GitHub.

- **Strategy** (`smc_core.py`): SMC on XAUUSD M5, New York session only -
  BOS, order block, fast impulse / slow pullback, liquidity sweep, entry at the
  OB touch, SL behind the OB, TP 4R, break-even at +1R.
- **Fix - live = backtest**: the live bot now feeds EVERY closed candle to the
  setup tracker (also with a position open, outside the session or after the
  daily cap), replays ~200 candles at start-up and catches up after a stall.
  Checked by `tests/test_live_parity.py`: 233/233 trades identical over 65,000
  candles (the old loop differed on 28).
- **New option - step trailing stop** (`trail_steps`, off by default): +2R ->
  SL to +1R, +3R -> SL to +2R. Backtest and core only, the live bot does not
  trail yet. 12-month comparison: `tests/compare_trailing.py`.
