# Changelog

Every push to GitHub gets a new version: the number is in `VERSION`, the same
number is a git tag (`v1.0.0`, ...), and a short note goes here.

`MAJOR.MINOR.PATCH` - PATCH = a fix, MINOR = a new option or feature,
MAJOR = a change that alters how the bot trades by default.

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
