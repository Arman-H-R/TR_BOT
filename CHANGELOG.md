# Changelog

Every push to GitHub gets a new version: the number is in `VERSION`, the same
number is a git tag (`v1.0.0`, ...), and a short note goes here.

`MAJOR.MINOR.PATCH` - PATCH = a fix, MINOR = a new option or feature,
MAJOR = a change that alters how the bot trades by default.

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
