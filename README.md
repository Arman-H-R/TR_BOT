# SMC bot for XAUUSD - four files

> **v2.0.0** (2026-11-18): three rule changes chosen by a large, out-of-sample
> checked study - flat by **Friday 16:00 New York**, the touch candle must
> **close back outside the order block**, break-even at **+2R** - plus realistic
> gap fills in the backtest and saved per-position risk in the live bot.
> Results, risks and how to reproduce everything: [`research/RESULTS.md`](research/RESULTS.md).
> Short version: 17 months, realistic costs, v1 +5.9 R / max DD 18.5 % ->
> v2 +77 R / max DD 9.7 %, positive in both halves - but the same logic lost
> money on 2022-2024 M15 history, so forward-test on demo first.

| File | What it is |
|---|---|
| [`smc_core.py`](smc_core.py) | **the strategy.** No broker, no files, no CLI - just the rules. |
| [`live_trade.py`](live_trade.py) | runs the core live on MetaTrader 5. |
| [`backtest.py`](backtest.py) | runs the same core bar by bar over history. |
| `README.md` | this file. |

Nothing else is needed. The live bot and the backtester both drive
`smc_core.SetupTracker`, so a rule change cannot apply to one and not the other.
XAUUSD only, New York session only, M5 only.

## The rules, and where they live

| # | Rule | Function in `smc_core.py` |
|---|---|---|
| 1 | **BOS** - the first candle that *closes* beyond an unbroken swing high / low. BOS and CHOCH are both just a break. Each swing can be broken only once. | `find_bos_events()` |
| 2 | **OB** - walking back from the break, the last candle whose colour is opposite to the impulse (buy setup -> last red candle). Zone = that candle's full range. | `find_order_block()` |
| 3 | **Fast impulse, slow pullback** - the return must take >= impulse + 2 candles, so the spec's "BOS in 3 candles -> back in 5 or more" holds. The absolute rails (`max_impulse_bars`, `min_pullback_bars`) change nothing measurable. | `find_signal()` |
| 4 | **LQ** - two or more approximately equal lows (floor) / highs (ceiling) **within 1.00 USD**, which price sweeps on its way to the OB. That width comes from the spec's own example: a floor of 4999 and 5000 is one pool. | `find_liquidity()` |
| 5 | **Entry** - the setup is watched from the break and re-checked on **every closed candle**; when price touches the OB the setup is either valid (trade) or not (see below). One entry per OB. | `SetupTracker.update()`, `check_setup()` |
| 6 | **SL / TP / BE** - SL = far side of the OB -/+ 4 pips. R = \|entry - SL\|. TP = entry +/- **4R** (`Config.rr`). At **+2R** (`be_trigger_r`, v2; was 1R) the SL moves to entry +/- 2 pips, so break-even is real after the spread ("tax"). | `build_trade()`, `apply_breakeven()` |
| 7 | **Filters** - XAUUSD only; 08:00-17:00 `America/New_York`, DST handled; weekdays only; max 3 trades per day. | `in_session()`, `DayLimit` |
| 8 | **v2: confirmation** - the candle that touches the OB must close back outside it (above the top for a buy, below the bottom for a sell); otherwise that touch is ignored and the setup keeps waiting (`entry_confirm_close`). | `SetupTracker.update()` |
| 9 | **v2: Friday** - no new trade from Friday 16:00 New York, and an open position is closed then (`friday_close`); a position carried over a weekend anyway is closed at the first chance. | `friday_cutoff()` |

Rule 6 on the numbers from the spec: entry `5000`, SL `4990` -> R `10`, and at
`5020` (+2R) the stop jumps to `5000.20`. TP is `5040` at the 4R default.

## LQ: how a pool of equal lows is recognised (rule 4)

Several roughly equal lows (or highs) count as ONE pool. `_clusters()` groups every
swing low within `lq_tolerance_pips` of each other and reports the band plus how
many times price touched it - so three floors 2.9 pips apart are a single LQ with
three touches, not three separate levels.

Checked on a rebuilt copy of the chart you sent (lows at 4012.0, 4012.2, 4011.9,
an order block at 3998-4002 below them, price sweeping the band and returning):

```
swing lows found: [(6, 3998.0), (12, 4012.2), (15, 4011.9)]
band anywhere above the OB      ->  LQ 4012.05 with 2 touches
band must be under the BOS      ->  NOT DETECTED
```

That is the bug you spotted. The old rule only accepted a pool sitting between the
order block and the level the BOS broke, and in your screenshot the equal floors
are ABOVE that level, so the pool was thrown away. Over five months the same rule
discarded 663 pools.

Three settings now control it:

| setting | what it does |
|---|---|
| `lq_require_between_bos` | True = the old, narrow rule; False = any pool price had to pass through to reach the OB |
| `lq_max_distance_pips` | how far above (below) the order block the pool may sit; 0 = no limit |
| `lq_sweep_within_bars` | the sweep must have happened within this many candles of the entry; 0 = no limit |
| `lq_tolerance_pips` | how far apart two floors may be and still count as one band |

### What the two readings are worth

Same 44 weeks of XAUUSD M5, same everything except the LQ rule:

| rule | trades/week | avg R | total R | older half | newer half |
|---|---|---|---|---|---|
| narrow, lookback 40 (the original) | 4.0 | +0.187 | +33.0 R | +0.114 | +0.293 |
| **narrow, lookback 120 (shipped)** | **5.4** | **+0.108** | +25.8 R | +0.031 | +0.220 |
| wide (any pool above the OB), 30 pips / 10 candles | 4.5 | +0.036 | +7.2 R | +0.089 | -0.036 |
| wide, no distance or recency limit | 10.7 | -0.059 | -27.8 R | -0.073 | -0.040 |

Two things came out of this:

* **Looking further back pays.** Keeping the narrow rule but searching 120 candles
  instead of 40 found pools that were being missed for no good reason: 4.0 -> 5.4
  trades a week with the edge intact in both halves. That is now the default.
* **Trading every pool above the order block does not pay.** It is the correct
  reading of your chart, and the detector handles it, but on 44 weeks it cut the
  average from +0.108 R to +0.036 R and turned the recent half negative. It ships
  switched off: `--set lq_require_between_bos=false` turns it on.

With the shipped settings, the last month (24 Sep - 28 Oct 2026) gives 32 trades,
6.4 a week, 50% winners, +0.261 R each, +8.3 R, profit factor 1.52, 4,700 ->
4,817 (+2.5%), worst losing run 5.0 R.

## What makes an order block GOOD (rule 2, measured)

Your rule 2 says a good OB is the small candle that created the BOS. `ob_quality()`
in `smc_core.py` turns that into a score out of 8, with every weight in `Config`
so any part can be switched off by setting it to 0.

Each part was measured over the full 17 months (308 trades, `ob_valid_bars=60`),
comparing the trades where it fired against the trades where it did not:

| part | what it means | fires | with it | without it |
|---|---|---|---|---|
| `sharp_impulse` | the impulse took <= 2 candles | 134 | **+0.304 R** | -0.190 R |
| `first_hour` | entry in the first New York hour | 78 | **+0.373 R** | -0.093 R |
| `small_ob` | OB height <= 20 pips (your "little candle") | 66 | +0.122 R | -0.001 R |
| `slow_return` | pullback >= 8x the impulse | 174 | +0.100 R | -0.072 R |
| `lq_strong` | the LQ pool has 3+ equal touches | 78 | +0.056 R | +0.015 R |

`lq_strong` is the one part that adds nothing - two equal lows are as good as
three. Liquidity itself still matters: rule 4 refuses the trade without it.

Raising `min_quality` raises the edge and lowers the frequency, almost in a
straight line (same 17 months, `ob_valid_bars=60`):

| min_quality | trades | per week | avg R | total R | older half | newer half |
|---|---|---|---|---|---|---|
| 0 (take everything) | 308 | 4.2 | +0.025 | +7.8 | -0.097 | +0.203 |
| 1 | 252 | 3.5 | +0.111 | +28.1 | -0.029 | +0.317 |
| 2 | 207 | 2.8 | +0.144 | +29.7 | -0.028 | +0.399 |
| 4 | 90 | 1.2 | +0.305 | +27.5 | +0.071 | +0.752 |
| 5 | 42 | 0.6 | +0.497 | +20.9 | +0.434 | +0.768 |

That is the trade-off in one table: **quality and frequency pull against each
other.** Quality 4 and 5 are the only rows that make money in BOTH halves of the
history, and they trade about once a week. You asked for four a week, which means
accepting roughly quality >= 1.

A good setup may also trade beyond the daily cap, which is your "when good setup
you can more trade it": `max_trades_per_day` is the normal limit, and a setup
scoring `good_quality` (5) or more may use `extra_trades_if_quality` (2) extra
slots.

### The settings that ship, and why

`ob_valid_bars = 80`, `bos_max_age = 80`, `min_quality = 1`.

A setup now has 80 candles to be touched instead of 40, which roughly doubles the
number of candidates, and `min_quality = 1` throws the worst of them away again.
That combination was the only one that improved BOTH numbers at once:

| setting | trades/week (17 months) | avg R | trades/week (last month) | avg R |
|---|---|---|---|---|
| old: valid 40, no quality filter | 3.3 | +0.067 | 4.5 | +0.137 |
| **valid 80, quality >= 1 (shipped)** | **4.2** | **+0.081** | **5.7** | **+0.281** |
| valid 80, quality >= 2 | 3.5 | +0.069 | 4.7 | +0.188 |
| valid 60, quality >= 4 | 1.2 | +0.305 | - | - |

Everything else that was tried for four trades a week was worse. Letting a setup
wait even longer keeps adding trades and losing edge, in a straight line:

| setting | trades/week | avg R | total R | older half | newer half |
|---|---|---|---|---|---|
| valid 80, quality >= 1 (shipped) | 4.2 | +0.081 | +24.9 | -0.025 | +0.244 |
| valid 120, quality >= 1 | 4.9 | +0.032 | +11.4 | -0.041 | +0.149 |
| valid 120, quality >= 2 | 4.0 | +0.020 | +5.9 | -0.074 | +0.171 |
| valid 160, quality >= 2 | 4.5 | -0.015 | -4.8 | -0.104 | +0.132 |
| valid 120, sharp impulse only | 2.5 | **+0.144** | +26.6 | **+0.043** | +0.282 |

The last row is the most robust thing in this whole study: requiring nothing but
an impulse of two candles or less (`--set quality_small_ob=0 --set
quality_lq_strong=0 --set quality_slow_return=0 --set quality_first_hour=0`) is
the ONLY configuration that was positive in the older half of the history as well
as the newer one. It trades 2.5 times a week, not four, and it lost money last
month (-0.118 R over 16 trades). If you ever decide frequency matters less than
robustness, that is the row to start from.

Last month (18 Sep - 26 Oct 2026, 5 weeks) with the shipped settings: 30 trades,
5.7 a week, 56.7% of them winners, +0.281 R each, +8.4 R in total, profit factor
1.65, equity 4,700 -> 4,850.59 (+3.2%), worst losing run 4.9 R.

Two honest warnings about that month. Thirty trades carry an error of about
+/-0.26 R on the average, so +0.281 and zero are not far apart. And over the full
17 months the same settings make +0.081 R per trade, with the older 60% of the
history slightly negative (-0.025) and the newer 40% clearly positive (+0.244) -
the edge such as it is lives in recent data, which is the oldest trap in this
business.

## Watching a setup until the touch (rule 5 in detail)

`SetupTracker` remembers every break and its order block, then on each closed
candle asks `check_setup()` whether the setup is still worth waiting for. Only two
things kill it while it waits:

* it ran out of time - `ob_valid_bars` candles since the break
* a candle **closed** beyond the far side of the block, so the zone is gone

When price finally touches the block, the setup is judged there and then:

| at the touch | `fail_on_touch = True` (your rule) | `fail_on_touch = False` |
|---|---|---|
| pullback too fast (rule 3) | the setup **fails**, no second chance | that touch is wasted; if price leaves and returns slowly it can still fire |
| no LQ taken yet (rule 4) | the setup **fails** | same - the touch is wasted, the setup waits |
| outside the session | the touch is skipped either way (positions already open keep running) |

Measured on XAUUSD M5, 0.5% risk, 3R target, balance 4,700:

| 17 months (Jun 2025 - Oct 2026) | trades | per week | win | avg R | total | worst run |
|---|---|---|---|---|---|---|
| `fail_on_touch = True` (your rule) | 121 | 1.7 | 47.1% | +0.082 R | +10.0 R (+2.0%) | 10.7 R |
| `fail_on_touch = False` | 239 | 3.3 | 47.3% | +0.080 R | +19.0 R (+6.4%) | 16.1 R |

| last 5 weeks only | trades | per week | win | avg R | total |
|---|---|---|---|---|---|
| `fail_on_touch = True` | 13 | 2.6 | 46% | +0.390 R | +5.1 R (+2.2%) |
| `fail_on_touch = False` | 24 | 4.8 | 50% | +0.137 R | +3.3 R (+1.5%) |

Read the 17-month rows, not the 5-week ones. Over a real sample the two rules
earn the SAME per trade (+0.082 against +0.080, well inside the noise), so the
strict rule simply trades half as often and therefore earns half as much in
total. The five-week gap that seemed to favour it was 13 trades of luck.

That is also the one place where your two wishes pull apart: the strict rule gives
1.7 trades a week, the lenient one 3.3, and you asked for at least 4. The default
here is `False` for that reason - flip it with `--set fail_on_touch=true` and the
backtest will show you the same comparison on whatever window you choose.

One rule that is **not** in your list and is therefore off by default:
`cancel_on_opposite_break`. Switching it on retires a setup as soon as structure
breaks the other way, which on M5 with `swing_lookback=2` happens constantly - it
killed 560 of 675 setups in five weeks and left no trades at all.

A detail worth knowing: taking the LQ pool (rule 4) usually closes beyond a minor
swing of its own, which *is* a break in the opposite direction. So the detector
scans recent breaks newest-first and takes the first one with a complete setup -
looking only at the newest break would find the sweep and never the setup.

## Install

```
pip install -r requirements.txt
```

`tzdata` is what makes `America/New_York` work on Windows. Without it the session
filter silently falls back to a fixed 13:00-21:00 UTC window (no DST).

## Backtest

```
python backtest.py --mt5 --symbol XAUUSD --balance 4700 --out reports/smc_m5
python backtest.py --csv data/XAUUSD_M5.csv --start 2026-01-01 --risk 0.25
```

CSV needs `time,open,high,low,close` (any case). `--out` writes `trades.csv` and
`equity.csv`. The terminal's "Max bars in chart" setting caps `--mt5`; the loader
halves the request until it is accepted (~65 000 M5 candles here).

The fill model is deliberately pessimistic: entry at the close of the signal
candle with the spread paid there, management starts on the next candle, and a
candle holding both the stop and the target counts as a **stop**.

### Measured, 65 000 real M5 candles (2025-11-21 -> 2026-10-23, NY only)

```
trades 154 (3.20 per week)  |  wins 48.7%  |  +16.07 R  |  profit factor 1.18
net +295.31 (+6.28% on 4700)  |  max drawdown 4.46%  |  worst run 8 losses
exits: 79 sl, 31 tp, 44 breakeven      (0.5% risk, 3-pip spread, no commission)
H1 +0.054 R/trade (71)   H2 +0.147 R/trade (83)
```

**The frequency / quality trade-off, same data and costs.** Two of the defaults
above are choices, not rules, and both are set the way the spec reads rather than
the way the backtest prefers:

| config | trades | /wk | win% | total R | exp. R | PF | max DD |
|---|---|---|---|---|---|---|---|
| first version: 2R, first touch only, 0.50 USD pool | 69 | 1.44 | 58.0 | +21.71 | +0.315 | 1.75 | 2.05% |
| as shipped: 3R, any touch, 1.00 USD pool | 154 | 3.20 | 48.7 | +16.07 | +0.104 | 1.18 | 4.46% |
| `lq_tolerance_pips = 5.0` (rejects the 4999/5000 example) | 108 | 2.25 | 53.7 | +29.52 | +0.273 | 1.58 | 2.67% |

More trades cost expectancy here. `entry_on_retouch` is the one change that paid
on both counts (68 -> 103 trades, +23.07 -> +26.37 R on its own); widening the
liquidity pool to the spec's 1.00 USD is what diluted it. Two one-line knobs:
`rr = 2.0` for the original target, `lq_tolerance_pips = 5.0` for the tighter pool.

Things that do **not** restrict frequency, measured: the 3-trades-a-day cap and
one-position-at-a-time (2 setups blocked out of 71), the impulse and pullback
rails (identical counts), and `min_sl_pips`.

Things that destroy it: `swing_lookback = 1` (153 trades, +0.045 R each),
`lq_min_touches = 1` i.e. no rule 4 at all (407 trades, **-41.6 R**), and
`lq_allow_pre_ob = True` (+39 trades worth -0.55 R).

What this is not, is proven. 154 trades at +0.104 R each is a t of +0.85 - well
inside noise, and weaker evidence than the 69-trade version it replaced.
Forward-test before trusting any of it with size.

## Live

```
python live_trade.py --dry-run
python live_trade.py --risk 0.25 --timeframe M5
```

`--dry-run` logs every signal and sends nothing. Live, it sends a market order
with the SL and the `Config.rr` TP (4R as shipped), then moves the stop to
break-even at +2R, and flattens on Friday at 16:00 New York.

* **same candles, same memory as the backtest.** Every closed candle goes to the
  tracker - also while a position is open, outside the New York session and after
  the daily cap; only the decision to *trade* is gated (flat, NY session, cap).
  It still only ever opens trades inside the NY window. On start-up it replays the
  last ~200 candles, and after a stall it replays the candles it missed, so a
  restart does not change what it trades. `tests/test_live_parity.py` checks this
  against `backtest.run()` on real MT5 history:
  `python tests/test_live_parity.py --bars 65000`.

* one position at a time, `--max-trades` per day counted from the broker's own
  deal history, so a restart cannot double up;
* the risk of every position is saved when it opens (`--state`,
  `reports/live_positions.json`, and the order comment `smc r=<risk>`), and the
  break-even / trail state is read off the position's current SL, so a restart -
  or changing `rr` while a position is open - loses nothing;
* volume is rounded **down** to the broker's step - it never over-risks;
* rejects a setup whose stop is outside 10-150 pips, or a spread above
  `--max-spread-pips`, or SL/TP closer than the broker's own stop level;
* refuses any symbol without `XAU` in the name.

Which account it trades is set in **Which account it trades** below.

## Which account it trades

Two ways, pick either. It trades that account whether it is demo or real - the
startup line says which, e.g.
`connected: YourBroker-Server #12345678 ... | DEMO account`.

**1. Fill it in at the top of [`live_trade.py`](live_trade.py)** and the bot logs
the terminal in itself:

```python
ACCOUNT = {
    "login": 12345678,
    "password": "your-password",
    "server": "YourBroker-Server",          # exactly as the terminal spells it
    "terminal": None,                        # path to terminal64.exe, if needed
}
```

**2. Or leave that block as `None` and log the MetaTrader 5 terminal in
yourself** - the bot attaches to whatever account is open there. This is the
default, and nothing is stored anywhere.

The command line (`--login 12345678 --password ... --server ...`) overrides the
block, and `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` / `MT5_PATH` in the
environment sit in between - handy because way 1 keeps your password in plain
text inside a source file.

For real orders the terminal's **Algo Trading** button must be on; the bot warns
at startup if it is off instead of letting every order be rejected.

## Forward test

```
python live_trade.py --dry-run     # a session or two first, just watching
python live_trade.py               # then let it trade
```

Leave it running. It only *opens* inside the New York window; outside it the loop
keeps managing whatever is already open.

### What a session looks like

On every closed candle inside the window it prints what the detector sees, so an
hour of no trades still tells you it is alive and why nothing fired:

```
19:45 UTC close 4122.79 | sell OB 4123.44-4125.69 (BOS 4121.08, 6b ago) | touch in 0.43 | no LQ yet | +3 more
20:00 UTC close 4122.06 | sell OB 4123.44-4125.69 (BOS 4121.08, 9b ago) | touch in 0.70 | +3 more
20:05 UTC close 4123.49 | sell OB 4123.44-4125.69 (BOS 4121.08, 10b ago) | AT the OB | +2 more
        SIGNAL SELL XAUUSD @~4123.44 SL 4126.09 | BOS 4121.08 in 3 bars, ...
```

Read it right to left: `pullback 4/5 bars` and `no LQ yet` are the rules still
missing, `touch in 0.43` is how far price is from the order block, `AT the OB` is
the trigger candle, `already tagged, spent` means that OB had its one chance, and
`+3 more` counts the other live setups it is tracking. A held position prints its
own line per candle with the running R.

Startup also prints the window in both clocks, e.g.
`next New York window: Mon 2026-10-26 12:00 - 21:00 UTC (Mon 15:30 - Tue 00:30 your time)`.
Note the window moves an hour in UTC when US daylight saving ends (1 Nov 2026):
`zoneinfo` handles that, which is why the session is defined in New York time and
not as fixed UTC hours.

Of the 69 backtest entries, 15 were in the first hour of the window and 13 in the
last; the middle is quieter. Those hourly buckets are far too thin to filter on -
they only tell you which hour is worth watching if you cannot watch all of it.

Every close is appended to `reports/forward_trades.csv` with the same columns as
the backtest's `trades.csv`, and each one logs the running tally next to the
number to beat:

```
CLOSED BUY at 4012.40 (tp) +2.00 R, +93.80 -> forward record: 12 trades,
+3.40 R total, +0.283 R average (backtest: +0.315)
```

* **Compare average R, not money** - the demo balance and your real one differ.
* **~70 trades** is the sample that would make the backtest's +0.315 R meaningful,
  roughly 11 months at 1.4 trades/week. To collect faster, run a second instance
  on `--timeframe M15 --magic 777002`: the magic number keeps the two bots'
  positions, break-evens and daily counters apart on the same symbol.
* If the forward average lands near +0.3 R, the backtest is holding up. Near zero
  or below after ~70 trades, the backtest was the sample talking - the pre-OB
  variant is what that failure looks like (+0.098 R on the second half).
* The journal records closes it is running for. If the bot is down when a trade
  closes, that row is missing - the trade itself is still in MT5's history.
* `--dry-run` writes no journal (there are no fills to record).

## Two things that will bite you

**MT5 candle times are broker time, not UTC.** Everything here works in real UTC
and converts for the session filter, so the offset must be right or the bot
trades the wrong hours. It is auto-detected from the last tick, but that tick is
stale while the market is closed, so an implausible guess is dropped with a
warning - pin it with `--server-offset 0` (this broker is UTC+0; most are +2/+3).
On the same data a wrong clock turned +21.16R into -14.86R.

**A "pip" of gold is `Config.pip_size = 0.10` USD**, the only place that unit is
defined. So the 4-pip stop buffer is 0.40 USD and 1.00 lot (100 oz) gains 100 USD
per 1.00 USD of price. This broker quotes XAUUSD with `point = 0.01` and a
100 oz contract, which matches. Check yours before going live.

## Tuning

Every number is a field of `smc_core.Config` - swing strength, how far back the
OB is hunted, impulse / pullback limits, the LQ tolerance and touch count, the
stop buffer, R:R, the break-even trigger and its cost, the session window, the
daily cap. The CLIs expose the ones you change often (`--risk`, `--max-trades`,
`--timeframe`, `--close-at-session-end`); edit `Config` for the rest.

---

# What was tested, and what the numbers said

Everything below is a real backtest on XAUUSD M5, **44 weeks (2025-12-24 .. 2026-10-30,
60,000 candles)**, NY session, 1 position at a time, market fills at the signal candle
plus live spread. `old` / `new` split the history 60/40 so you can see whether a result
survives in both halves - a setting that only works in one half is curve fitting.
`R/week` is the number that matters: edge per trade multiplied by how often it fires.

## Sessions: London does NOT help

| session (New York local) | n | /wk | win | avg R | R/week | total R | max DD | old | new | last 5wk |
|---|---|---|---|---|---|---|---|---|---|---|
| **NY 08:00-17:00 (shipped)** | 238 | 5.4 | 48% | **+0.113** | **+0.61** | +26.8R | 19.6R | +0.031 | +0.231 | +0.68 |
| London+NY 03:00-17:00 | 341 | 7.7 | 46% | +0.016 | +0.12 | +5.3R | 25.4R | -0.027 | +0.081 | -1.52 |
| London+NY, bonus at both opens | 344 | 7.8 | 46% | +0.038 | +0.30 | +13.1R | 15.5R | +0.016 | +0.073 | -1.52 |
| London 03:00-11:30 only | 245 | 5.5 | 44% | **-0.021** | -0.12 | -5.3R | 19.5R | -0.015 | -0.032 | -1.15 |
| London open 03:00-06:00 only | 91 | 2.1 | 45% | -0.042 | -0.09 | -3.8R | 9.7R | +0.010 | -0.143 | -0.80 |
| NY 08:00-12:00 | 137 | 3.1 | 47% | +0.077 | +0.24 | +10.5R | 14.0R | +0.046 | +0.124 | +1.25 |
| NY open 08:00-11:00 | 121 | 2.7 | 48% | +0.085 | +0.23 | +10.3R | 13.0R | +0.105 | +0.054 | +1.22 |

London produces just as many setups as NY and loses money on them, in both halves and
in the last 5 weeks. Adding it to NY costs edge (+0.113 -> +0.016R) **and** raises the
drawdown (19.6R -> 25.4R): more risk for less money. London is a frequency trap, not a
frequency solution. Session stays NY-only.

(London is expressed in NY time because the config has one timezone. 08:00-16:30 London
= 03:00-11:30 NY, exact except for the ~2 weeks a year when the UK and US switch DST on
different dates.)

## Entry at the wick or at the body

| entry zone | lookback | /wk | win | avg R | R/week | old | new | last 5wk |
|---|---|---|---|---|---|---|---|---|
| **wick (shipped)** | 40 | 4.0 | 50% | **+0.193** | **+0.77** | +0.114 | +0.308 | +0.50 |
| wick | 120 | 5.4 | 48% | +0.113 | +0.61 | +0.031 | +0.231 | +0.68 |
| body, stop behind wick | 40 | 4.1 | 47% | +0.138 | +0.57 | +0.073 | +0.236 | +2.47 |
| body, stop behind wick | 120 | 5.7 | 43% | +0.011 | +0.06 | -0.010 | +0.045 | +1.28 |
| body, stop behind body | 40 | 4.3 | 42% | +0.004 | +0.02 | -0.063 | +0.115 | +1.30 |
| body, stop behind body | 120 | 6.2 | 39% | -0.068 | -0.42 | -0.110 | +0.005 | -0.72 |

A body zone means price has to push deeper into the order block before you are filled,
and candles that go that deep more often keep going: the win rate falls 48% -> 43% with a
wick stop and -> 39% with a body stop. Body entry does look better over the **last 5 weeks
only** (+2.47 vs +0.50 R/week) - that is 27 trades, far too few to act on, and it
contradicts 44 weeks. Shipped as wick entry.

Two switches control this (`smc_core.py`):

- `ob_use_body` - False (default) uses the full candle range, True uses open..close.
- `sl_use_wick` - with `ob_use_body=True`, True keeps the stop behind the wick instead
  of the body. Added so the entry depth and the stop distance can be tested separately;
  before, one flag moved both and the two effects were impossible to separate.

## Liquidity lookback: the default was wrong, now fixed

`lq_lookback_bars` sets how far back the code looks for the equal highs/lows that form a
liquidity pool. Swept over the full 44 weeks:

| lookback | /wk | avg R | R/week | old | new |
|---|---|---|---|---|---|
| 20 | 3.3 | +0.305 | +1.00 | +0.248 | +0.390 |
| 30 | 3.8 | +0.159 | +0.60 | +0.080 | +0.276 |
| 40 | 4.0 | +0.191 | +0.76 | +0.114 | +0.300 |
| 50 | 4.2 | +0.160 | +0.67 | +0.082 | +0.268 |
| 60 | 4.4 | +0.142 | +0.63 | +0.042 | +0.289 |
| **80 (shipped)** | **4.9** | +0.153 | **+0.75** | +0.080 | +0.256 |
| 100 | 5.2 | +0.089 | +0.47 | +0.025 | +0.181 |
| 120 (was shipped) | 5.4 | +0.111 | +0.60 | +0.031 | +0.226 |
| 160 | 5.8 | +0.041 | +0.24 | -0.037 | +0.150 |

30-80 is a plateau, past 100 the edge decays. **20 scores highest but 30 dips below 40 on
either side of it - an isolated spike like that is noise, not an edge, so it is not
shipped.** 120 was a mistake on my part: it raised the trade count but cut the weekly
return by about 20%.

80 beats 40 on the >=4 trades/week rule at identical return:

| lookback | n | /wk | avg R | R/week | weeks with >=4 trades | median week | worst week |
|---|---|---|---|---|---|---|---|
| 40 | 176 | 4.1 | +0.193 | +0.79 | 27/43 = 63% | 4 | **0** |
| **80** | 216 | 5.0 | +0.160 | **+0.80** | 36/43 = **84%** | 5 | 1 |

## AMD (accumulation / manipulation / distribution): built, tested, shipped OFF

The model: price builds a range before the NY open (accumulation, 02:00-08:00 NY), the
open pushes through one side to take the stops there (manipulation, until 10:00), and the
real move goes the other way (distribution). A setup pointing *against* the sweep is the
one to take. Implemented in `day_amd()` and controlled by `amd_mode`
(`"off"` / `"filter"` / `"quality"`).

| AMD setting | n | /wk | win | avg R | R/week | total R | old | new |
|---|---|---|---|---|---|---|---|---|
| **off (shipped)** | 238 | 5.4 | 48% | +0.108 | **+0.58** | **+25.8R** | +0.031 | +0.220 |
| quality bonus +2 | 247 | 5.6 | 47% | +0.080 | +0.45 | +19.9R | +0.038 | +0.141 |
| filter: 02-08 range, sweep by 10:00 | 52 | 1.2 | 54% | **+0.300** | +0.35 | +15.6R | **+0.277** | **+0.342** |
| filter: asian 19-02 range | 0 | 0.0 | - | - | - | - | - | - |
| filter: 02-08, sweep by 12:00 | 48 | 1.1 | 44% | +0.008 | +0.01 | +0.4R | +0.111 | -0.162 |
| filter: 02-08, no reclaim needed | 62 | 1.4 | 52% | +0.156 | +0.22 | +9.7R | +0.143 | +0.178 |
| filter: 06-09 range, sweep by 11:00 | 54 | 1.2 | 46% | -0.026 | -0.03 | -1.4R | +0.109 | -0.209 |

**AMD works exactly as the theory says** - it more than doubles the edge per trade
(+0.108 -> +0.300R) and is the only setting that is positive in both halves. But it fires
1.2 times a week against your 4/week minimum, and earns less in total (+15.6R vs +25.8R)
because frequency collapses. Every attempt to buy the frequency back failed:

| AMD + loosening something else | /wk | avg R | R/week |
|---|---|---|---|
| AMD filter alone | 1.2 | +0.300 | +0.35 |
| + min_quality 0 | 1.3 | +0.181 | +0.24 |
| + wide LQ | 3.6 | **-0.140** | -0.50 |
| + wide LQ + 6 trades/day | 3.6 | -0.120 | -0.43 |
| + OB valid 160 bars | 1.9 | +0.097 | +0.18 |
| + manipulation window to 12:00 | 1.3 | -0.078 | -0.10 |
| + no reclaim required | 1.6 | +0.069 | +0.11 |
| + lookback 40 | 0.9 | +0.083 | +0.08 |

The code stays in place behind `amd_mode="off"`. Set it to `"filter"` if you ever want to
trade once a week at a much higher hit rate; it is not the route to a higher monthly
return.

## "An order block is one candle" - verified, and the ambiguity it hides

Checked directly: over 30,000 candles the code built **2,834 order blocks and all 2,834
zones match exactly one candle's high and low.** A zone never spans two candles, with
either `ob_use_body` setting. The one-candle rule was never broken.

What you were seeing on the chart is real, though, and it hid a decision nobody had
tested. The BOS leg often *starts* with a run of several opposite-coloured candles:

| opposite candles in a row at the OB | share |
|---|---|
| 1 | 58% |
| 2 | 26% |
| 3 or more | 15% |

So **42% of the time the order block looks like 2-3 candles on the chart**, and the code
has to choose one of them. It was silently taking the last one (nearest the BOS). That is
now the `ob_pick` setting, and the zone stays a single candle in every mode:

| `ob_pick` | n | /wk | win | avg R | R/week | total R | max DD | old | new |
|---|---|---|---|---|---|---|---|---|---|
| **"last" - nearest the BOS (shipped)** | 216 | **4.9** | 50% | +0.160 | **+0.78** | **+34.5R** | 14.6R | +0.089 | +0.262 |
| "first" - started the run | 150 | 3.4 | 51% | **+0.199** | +0.67 | +29.8R | 16.6R | +0.092 | +0.342 |
| "extreme" - lowest low / highest high | 210 | 4.7 | 50% | +0.155 | +0.73 | +32.5R | 13.7R | +0.076 | +0.269 |
| "biggest" - widest high-low | 176 | 4.0 | 48% | +0.136 | +0.54 | +23.9R | 13.6R | +0.048 | +0.257 |

`"last"` wins on weekly return and is the only one comfortably above 4 trades/week, so the
default is unchanged. `"first"` has the best edge per trade (+0.199R) and the best newer
half, but fires 3.4 times a week - worth trying only if the >=4/week rule is dropped.

## About the 8% per month target

The strategy produces **R**. 8% is then a position-sizing choice, not something a filter
delivers:

The shipped config measures **+0.160 R per trade over 216 trades, 4.9/week**, with a
**worst losing run of 14.6 R**. That run is the number that decides your risk, not the
average:

| measured edge | R/month | risk/trade needed for 8% | the 14.6R run already in this data | a typical 5R run |
|---|---|---|---|---|
| **shipped (+0.160R, 4.9/wk)** | 3.4R | **2.35%** | **-34%** | -12% |
| lookback 40 (+0.193R) | 3.4R | 2.35% | -34% | -12% |
| AMD filter (+0.300R, 1.2/wk) | 1.5R | 5.3% | -77% | -27% |
| **at the shipped 0.5% risk** | 3.4R | - | **-7.3%** | -2.5% |

At the shipped 0.5% risk this makes **1.6%/month** (measured: +15.6% over 10.1 months,
4,700 -> 5,431). Reaching 8% means risking about 2.35% per trade, and the 14.6R losing run
already present in this history would then cost 34% of the account. `risk_percent` is one line in the config and the choice is
yours, but understand that 8%/month on a +0.16R edge is a leverage decision, not an edge
improvement. The honest route to 8% is a higher edge, and the searches above are what
raising it looks like: mostly dead ends, with the occasional +0.1R.

## Caveats that apply to every table here

- One broker's spread, one symbol, one 44-week window. 2026 gold was unusually trending.
- Many settings were tried. Some of the better numbers are luck; that is exactly why
  every table shows the old/new split, and why the lookback-20 spike was rejected.
- 5-week numbers are 25-35 trades. They are reported because you asked for them, but they
  cannot distinguish a good setting from a lucky one.
- No slippage beyond spread, no commission in the R figures, no requotes, no weekend gaps
  against an open position.
