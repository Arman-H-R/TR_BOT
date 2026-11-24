# Config parameter audit (smc_core.Config, 71 fields)

Scope: `smc_core.py`, `backtest.py`, `live_trade.py`, `research/engine.py` (working copy, incl. the uncommitted overnight-session change in `eligible`), `research/v2.py` (`params_from_config`), `research/cache.py` (`STRUCT`). Read-only audit; nothing was run against MT5.

Abbreviations. **TR** = `SetupTracker.update` (the only signal path used by backtest and live). **BT** = `backtest.run`. **LT** = `live_trade.py`. **FS** = `find_signal` / module-level `state_line` (dead code: no caller in BT, LT, research or tests). **E-STRUCT** = in `cache.STRUCT` (stage-1 candidates, cache key). **E-Params** = mapped by `v2.params_from_config` to `engine.Params` (stage 2). **NO** = engine ignores it, needs `backtest.run`.
Units: pip = 0.10 price on gold; bar counts are M5 candles (12 = 1 h).

## 1. Field table

| # | name | group | default | type / options | where used | active at defaults? | engine | dependencies / interactions | sweep values |
|---|---|---|---|---|---|---|---|---|---|
| 1 | symbol | instrument | "XAUUSD" | str | LT `run`, `send_order`, `get_bars`; BT report only | yes (label in BT) | NO (data file fixed `XAUUSD_<tf>.csv`) | LT sets it from `--symbol`; BT from `--symbol` | fixed |
| 2 | timeframe | instrument | "M5" | str: M1 M5 M15 M30 H1 H4 (LT `TIMEFRAMES`) | LT `get_bars`; BT prints only (BT loads `args.timeframe`, never copies it into cfg) | LT yes; BT label only | NO (cache `tf` arg) | every bar-count field scales with it | fixed (M5) |
| 3 | pip_size | instrument | 0.10 | float | `Config.pips/to_pips` everywhere | yes | yes via Params (`pip`); **not in STRUCT** although stage 1 uses it (lq tolerance/distance) | unit for every *_pips field | fixed |
| 4 | contract_size | instrument | 100.0 | float | `lot_size`, BT pnl/risk_money, LT overwrites from broker | yes | Params `contract` (commission only) | BT ignores `spec["contract_size"]` from MT5 | fixed |
| 5 | swing_lookback | structure | 2 | int (>=1; <1 -> no swings) | `find_swings`, `find_bos_events`, `required_bars`, TR, `prime` | yes | E-STRUCT | pivot visible k bars late; drives BOS frequency, LQ pivots, window size (4k) | 1,2,3,4,5 |
| 6 | bos_max_age | structure | 80 | int bars | FS (`min(bos_max_age, ob_valid_bars)`), `required_bars` | **only window length** (TR never reads it) | E-STRUCT | in TR its only effect is `need`; if `ob_valid_bars > bos_max_age + 3k + 19` the OB / LQ pivots can fall off the window (setup fails "OB fell out of the window") | keep >= ob_valid_bars; 80,120,160 |
| 7 | ob_search_bars | order block | 20 | int bars | `find_order_block`, `required_bars` | yes | E-STRUCT | upper bound of impulse length; caps `max_impulse_bars` | 5,10,15,20,30 |
| 8 | ob_pick | order block | "last" | enum: last, first, extreme, biggest (else ValueError, raised only when a run exists) | `find_order_block` | yes ("last") | E-STRUCT | only differs when the opposite run is >=2 candles | last, first, extreme, biggest |
| 9 | ob_use_body | order block | False | bool | `find_order_block`, `stop_edge` | off | E-STRUCT | enables `sl_use_wick` | F, T |
| 10 | sl_use_wick | order block | True | bool | `OrderBlock.stop_edge`, `PendingSetup.stop_edge`, `candidates_fast` | **inactive** (needs ob_use_body=True) | E-STRUCT | only with ob_use_body | F, T (with body) |
| 11 | ob_require_small_body | order block | False | bool | `find_order_block` | off | E-STRUCT | enables ob_max_body_ratio | F, T |
| 12 | ob_max_body_ratio | order block | 1.0 | float | `find_order_block` | **inactive** (needs ob_require_small_body) | E-STRUCT | body(OB) <= ratio x mean impulse body | 0.3,0.5,0.75,1.0,1.5 |
| 13 | ob_valid_bars | order block | 80 | int bars | TR (registration + expiry), FS | yes | E-STRUCT | must be <= bos_max_age(+~3k+19) or window truncates; also bounds slow_return | 24,36,48,60,80,100,120 |
| 14 | max_impulse_bars | pullback-entry | 20 | int bars | `find_order_block` | **inactive**: impulse <= ob_search_bars=20 always | E-STRUCT | only bites if < ob_search_bars | 3,5,8,12,20 |
| 15 | min_pullback_bars | pullback-entry | 3 | int bars | `_setup_from_bos`, `needed_pullback`, `candidates_fast` | **inactive**: impulse>=1 so impulse+2>=3 | E-STRUCT | max(min_pullback, impulse+extra) | 3,5,8,12,20 |
| 16 | pullback_extra_bars | pullback-entry | 2 | int bars | same as 15 | yes | E-STRUCT | the real rule 3 | 0,1,2,3,4,6 |
| 17 | entry_on_retouch | pullback-entry | True | bool | **FS only** (`_setup_from_bos`); BT report print and `VARIANTS` | **UNUSED in trading path** (TR always allows re-touch via `inside_zone`) | not in STRUCT/Params (correctly irrelevant) | BT `--compare` row "first touch only" is a no-op | drop / re-implement |
| 18 | fail_on_touch | pullback-entry | False | bool | TR (too fast / no LQ / low quality) | yes (False branch) | **NO** (CandidateTracker never fails) | True kills setup at first bad touch; session/trend/confirm/AMD rejections never fail | F, T (BT only) |
| 19 | cancel_on_opposite_break | pullback-entry | False | bool | TR, CandidateTracker, candidates_fast | off | E-STRUCT | — | F, T |
| 20 | lq_tolerance_pips | liquidity | 10.0 | float pips | `find_liquidity` / `_clusters`, pre-OB sweep depth | yes | E-STRUCT | also the pre_ob sweep depth (`level -/+ tol`) | 3,5,7,10,15,20 |
| 21 | lq_min_touches | liquidity | 2 | int | `_clusters` | yes | E-STRUCT | <= lq_strong_touches for quality | 2,3,4 |
| 22 | lq_lookback_bars | liquidity | 80 | int bars | `find_liquidity` (lo = ob.index - n), `required_bars` | yes | E-STRUCT | also lengthens window | 20,40,60,80,120 |
| 23 | lq_require_between_bos | liquidity | True | bool | `find_liquidity` | yes | E-STRUCT | False uses `beyond` (anywhere past OB edge) | T, F |
| 24 | lq_max_distance_pips | liquidity | 0.0 | float pips, 0 = off | `find_liquidity` (pullback branch only) | **off** (0) | E-STRUCT | not applied to pre_ob pools | 0,30,50,100,200 |
| 25 | lq_sweep_within_bars | liquidity | 0 | int bars, 0 = off | `find_liquidity` | **off** (0) | E-STRUCT | measured to `upto`=first valid touch (lq cached afterwards) | 0,3,6,12,24 |
| 26 | lq_allow_pre_ob | liquidity | False | bool | `find_liquidity` | off | E-STRUCT | different sweep definition (needs > tol beyond) | F, T |
| 27 | sl_buffer_pips | risk-exits | 4.0 | float pips | TR (Signal.sl), FS, engine `simulate` | yes | Params | SL distance -> min/max_sl rails, R | 0,2,4,6,10,15 |
| 28 | rr | risk-exits | 4.0 | float R | `build_trade`, LT `PositionState` fallback | yes | Params | trail can never exceed TP | 2,2.5,3,4,5,6 |
| 29 | be_trigger_r | risk-exits | 2.0 | float R (no off sentinel; use 99) | `build_trade`, `apply_breakeven`, LT `trade_from_position` | yes | Params `be_r` (**engine: 0 = off; core: 0 = BE on first bar**) | interacts with trail_start_r | 1,1.5,2,2.5,3,99 |
| 30 | be_cost_pips | risk-exits | 2.0 | float pips | `build_trade`, LT `trade_from_position`, Journal | yes | Params | must be < risk or BE never applies (`be_price > sl` check) | 0,1,2,3,5 |
| 31 | trail_steps | risk-exits | False | bool | `apply_trailing` (BT + LT) | off | Params (trail_start=0 when False) | enables 32, 33 | F, T |
| 32 | trail_start_r | risk-exits | 2.0 | float R | `apply_trailing` | **inactive** (trail_steps) | Params (**0 means off in engine; in core 0 is a valid start**) | lock = k - gap must be > 0 to move | 1,1.5,2,2.5,3 |
| 33 | trail_gap_r | risk-exits | 1.0 | float R | `apply_trailing` | **inactive** (trail_steps) | Params | start-gap <= be lock -> no move | 0.5,1,1.5,2 |
| 34 | min_sl_pips | risk-exits | 4.0 | float pips | `build_trade` | yes (risk >= ~4.x pips by construction mostly) | Params | applied to fill-to-SL distance, after setup is spent | 4,10,15,20,30 |
| 35 | max_sl_pips | risk-exits | 150.0 | float pips | `build_trade` | yes | Params | idem | 60,80,100,150,250 |
| 36 | risk_percent | risk-exits | 0.5 | float % | `lot_size` | yes | NO (R-based, compounding approx) | LT **always overwritten by `--risk` (default 0.5)** | fixed (not an edge parameter) |
| 37 | session_tz | session | "America/New_York" | str IANA tz | `_tz`, `in_session`, `friday_cutoff`, `DayLimit`, `day_amd`, `ob_quality` | yes | **NO** (engine hard-codes NY) | all clock fields are local to it | fixed |
| 38 | session_start | session | 08:00 | time (local) | `in_session`, `session_window` | yes | Params `sess_start` | wrap (start>end) supported in core and engine `eligible`, not in engine close_session_end | 03:00,07:00,08:00,08:30,09:30 |
| 39 | session_end | session | 17:00 | time (local) | idem | yes | Params `sess_end` | idem; with close_at_session_end | 11:00,12:00,14:00,15:00,17:00 |
| 40 | utc_start | session | 13:00 | time UTC | `in_session` fallback | **inactive** (only without tzdata) | NO | — | fixed |
| 41 | utc_end | session | 21:00 | time UTC | idem | **inactive** | NO | — | fixed |
| 42 | trade_weekends | session | False | bool | `in_session`, `session_window` | yes (False) | **NO** (Params.weekdays fixed Mon-Fri) | — | fixed False |
| 43 | close_at_session_end | session | False | bool | BT (only if require_session), LT | off | Params `close_session_end` | LT **always overwritten by `--close-at-session-end` flag**; engine ignores overnight wrap | F, T |
| 44 | quality_small_ob | quality | 2 | int weight | `ob_quality`, `max_quality` | yes | Params `q_small` | weights matter only vs min_quality / good_quality | 0,1,2,3 |
| 45 | quality_lq_strong | quality | 2 | int weight | idem | yes | Params `q_lq` | idem | 0,1,2,3 |
| 46 | quality_sharp_impulse | quality | 1 | int weight | idem | yes | Params `q_sharp` | idem | 0,1,2,3 |
| 47 | quality_slow_return | quality | 1 | int weight | idem | yes | Params `q_slow` | idem | 0,1,2,3 |
| 48 | quality_first_hour | quality | 2 | int weight | idem (also second open) | yes | Params `q_first` | idem | 0,1,2,3,4 |
| 49 | ob_small_pips | quality | 20.0 | float pips | `ob_quality` | yes | Params | zone height (body if ob_use_body) | 10,15,20,30,40 |
| 50 | lq_strong_touches | quality | 3 | int | `ob_quality` | yes | Params `lq_strong` | must be > lq_min_touches to discriminate | 3,4,5 |
| 51 | sharp_impulse_bars | quality | 2 | int bars | `ob_quality` | yes | Params `sharp_bars` | <= ob_search_bars | 1,2,3,4 |
| 52 | slow_return_ratio | quality | 8.0 | float x impulse | `ob_quality` (age >= ratio x impulse) | yes | Params `slow_ratio` | age capped by ob_valid_bars | 3,5,8,12 |
| 53 | first_hour_start | quality | 8 | int NY hour | `ob_quality` | yes | Params (x60) | — | 7,8,9 |
| 54 | first_hour_end | quality | 9 | int NY hour | `ob_quality` | yes | Params (x60) | > start | 9,10,11 |
| 55 | second_open_start | quality | -1 | int hour, -1 = off | `ob_quality` | **off** | **NO** | needs second_open_end; scores quality_first_hour | -1, 2, 3 |
| 56 | second_open_end | quality | -1 | int hour | `ob_quality` | **off** | **NO** | idem | -1, 4, 5 |
| 57 | min_quality | quality | 1 | int (0 = all) | TR | yes | Params | max score = 8 (+amd_quality); with 1 every weight >0 is equivalent | 0,1,2,3,4,5 |
| 58 | amd_mode | AMD | "off" | enum: off, filter, quality (any other string = computed but no effect) | TR, `max_quality` | **off** | Params `amd_mode`, but engine handles only "filter"; **"quality" = off in engine** | enables 59-64 | off, filter, quality |
| 59 | amd_accum_start | AMD | 02:00 | time (local) | `day_amd` | **inactive** (amd_mode) | **NO** (engine hard-codes 120 min) | — | 00:00,02:00,03:00 |
| 60 | amd_accum_end | AMD | 08:00 | time (local) | `day_amd` | **inactive** | **NO** (480) | should be <= session_start or bias is 0 until it | 07:00,08:00,08:30 |
| 61 | amd_manip_end | AMD | 10:00 | time (local) | `day_amd` | **inactive** | **NO** (600) | — | 09:30,10:00,11:00 |
| 62 | amd_reclaim | AMD | True | bool | `day_amd` | **inactive** | **NO** (True) | — | T, F |
| 63 | amd_min_range_pips | AMD | 20.0 | float pips | `day_amd` | **inactive** | **NO** (2.0 price) | — | 10,20,40,60 |
| 64 | amd_quality | AMD | 2 | int weight | TR, `max_quality` | **inactive** (needs amd_mode="quality") | **NO** | — | 1,2,3 |
| 65 | trend_filter | trend | "off" | enum: off, H1, H4 (case-sensitive; else KeyError) | `trend_series`, `trend_from_htf`, TR, LT `htf_trend` | **off** | Params `trend_tf`/`trend_mode` | enables 66 | off, H1, H4 |
| 66 | trend_ema | trend | 50 | int HTF bars | `trend_from_htf`, LT `htf_trend` (needs 3x bars, max 600) | **inactive** | **NO** (engine EMA hard-coded 50, D1 20) | LT fails if trend_ema > 200 (600 bars / 3) | 20,50,100,200 |
| 67 | entry_confirm_close | pullback-entry | True | bool | TR | yes | Params `confirm` | a touch that fails it still consumes the visit (`inside_zone`) | T, F |
| 68 | friday_close | session | 16:00 | time or None (BT `--set` accepts none/off) | `friday_cutoff` (BT entry+exit, LT entry+exit) | yes | Params (minutes, **0 = off, so 00:00 is lost**) | — | None,12:00,14:00,16:00 |
| 69 | max_trades_per_day | daily limits | 3 | int | `DayLimit` (BT), LT `trades_today` cap | yes | Params `max_per_day` | + extra if quality >= good_quality | 1,2,3,4,6 |
| 70 | extra_trades_if_quality | daily limits | 2 | int | `DayLimit`, LT | yes | Params `extra_q` | only if good_quality reachable (<= max_quality) | 0,1,2 |
| 71 | good_quality | daily limits | 5 | int | `DayLimit`, LT | yes (reachable, max 8) | Params `good_q` | quality weights | 3,4,5,6,99 |

Note: weights/thresholds of quality only change results through `min_quality` (filter) and `good_quality` (cap). At `min_quality=1` any part with weight > 0 is enough, so weight values only matter for the cap.

## 2. Unused / one-sided parameters

| field | finding | status |
|---|---|---|
| entry_on_retouch | Read only by `_setup_from_bos` (smc_core.py:841), reached only from `find_signal`, which nothing calls. TR always allows a re-touch after price leaves the zone (smc_core.py:1318-1324). `backtest.py:338` prints it and `backtest.py:465` "first touch only" variant changes nothing. | verified, unused |
| bos_max_age | TR never reads it; only `find_signal`/`state_line` (dead, smc_core.py:786, 880) and `required_bars` (smc_core.py:282). Its sole effect is the window length (`need`) and BT/engine start bar. | verified, effectively unused |
| max_impulse_bars | `impulse = b - idx` with `idx >= b - ob_search_bars` (smc_core.py:577-615), so `impulse <= 20 = max_impulse_bars`. | verified, inactive at defaults |
| min_pullback_bars | `max(3, impulse+2)` with impulse >= 1 is always `impulse+2`. | verified, inactive at defaults |
| utc_start / utc_end | fallback only if `zoneinfo`/tzdata missing (smc_core.py:403). | verified |
| second_open_start/end | -1 sentinel; not mapped to engine. | verified |
| sl_use_wick, ob_max_body_ratio, trail_start_r, trail_gap_r, amd_* (6), trend_ema, lq_max_distance_pips, lq_sweep_within_bars | gated off by another flag/sentinel at defaults (see table). | verified |
| risk_percent, close_at_session_end | LT builds `Config(... risk_percent=args.risk, close_at_session_end=args.close_at_session_end)` (live_trade.py:764-766): editing the Config default changes BT but **not** live (flag defaults 0.5 / False win). | verified, BT/LT inconsistency |
| any other field | LT has no `--set`; every strategy change must be edited into the Config defaults (BT has `--set`). Sweep winners passed via `--set` are not what live trades. | verified |
| timeframe | BT never copies `--timeframe` into cfg (label only, backtest.py:332); LT does. | verified, cosmetic |
| contract_size | LT overwrites from broker (live_trade.py:532); BT ignores `spec["contract_size"]` (backtest.py:78 vs 263). R unaffected; money P&L wrong if broker != 100. | verified, low |
| smc_core.py:181 comment | "live_trade.py does not trail yet" is stale: LT calls `apply_trailing` (live_trade.py:446). | verified, doc only |

## 3. Implementation / parity / look-ahead findings

| # | finding | file:line | status | severity |
|---|---|---|---|---|
| 1 | Swings: pivot at p needs bars p+1..p+k; `find_bos_events` uses it only when `p + k <= i`; `find_liquidity` pivots come from a window ending at i (window pivots <= i-k). No look-ahead. | smc_core.py:476-503, 525-530 | verified OK | — |
| 2 | BOS = close of a closed bar beyond the most recent visible swing; signal bar i is closed; BT fills at `close[i]+spread`, LT drops the forming candle (live_trade.py:161). No look-ahead. | backtest.py:227-252 | verified OK | — |
| 3 | HTF trend: HTF candle usable from open+length, i.e. one M5 bar later than strictly possible (conservative). No leakage. | smc_core.py:441-467 | verified OK | — |
| 4 | **H4 bucket alignment differs BT vs LT**: BT resamples M5 to `4h` bins anchored at UTC midnight (00,04,08..); LT uses broker H4 candles anchored to server midnight. Identical only while the server is UTC+0 (it is now: log shows "server clock = UTC+0", data weekly open Sun 22:00/23:00 UTC). A UTC+2/+3 broker would give different H4 trend. | smc_core.py:453 vs live_trade.py:169-175 | verified (code); impact suspected only for non-UTC servers | medium if trend_filter=H4, else none |
| 5 | Server offset estimated once at LT start (live_trade.py:533); a broker DST switch while running would shift session/Friday/AMD clocks by 1 h until restart. Data files and BT `--mt5` treat server time as UTC with no offset (backtest.py:74, research/dump_data.py:15) - correct only because the server is UTC+0. | live_trade.py:533, backtest.py:74 | verified code; risk suspected | low now, high if broker changes |
| 6 | AMD: only bars <= i of today are read; manipulation window cannot complete before amd_manip_end, so bias can change intraday but never uses future bars. Engine `amd_bias` uses the final accumulation range but zeroes bias before accum_end, so also no leakage. | smc_core.py:1044-1101, engine.py:294-324 | verified OK | — |
| 7 | LQ "sweep" = any low below the MEAN level of the pool (`bars.low[j] < level`), checked from `last+1`, i.e. it can be a candle inside the last pivot's own k-bar confirmation and only a fraction of a pip below the mean, never beyond the lowest equal low. Pre-OB branch instead requires `level - tol`. Inconsistent and weak sweep definition (not leakage). | smc_core.py:707-711 vs 719-720 | verified | medium (strategy semantics) |
| 8 | `entry_confirm_close` / session / trend / AMD / quality rejections happen after `inside_zone=True` (smc_core.py:1321-1323): a wick into the zone that closes inside "uses" the visit; a rejection close on the next candle that still overlaps the zone is ignored. Same in engine (consistent), but it is a hidden entry rule. | smc_core.py:1316-1361 | verified | medium (design) |
| 9 | `lq_seen` is computed once at the first touch with enough pullback and then cached; `lq_sweep_within_bars` is measured to that touch, not to the later entry. | smc_core.py:1344-1345 | verified | low |
| 10 | A setup is spent (`_done_obs`, state "traded") when the tracker fires, even if BT/LT then skip it (position open, daily cap, Friday, SL rails, spread). Consistent BT/LT/engine. | smc_core.py:1390-1391, backtest.py:236-260 | verified | info |
| 11 | Only the first of several same-bar signals is returned; the others are spent. Consistent with engine `select`. | smc_core.py:1393-1394 | verified | low |
| 12 | Break-even/trail: BT applies on the bar range only when neither SL nor TP was hit and effective from next bar (a bar reaching +2R and the stop counts as full SL); LT applies on a 5 s polled tick immediately. LT can be better (BE saved) or miss spikes between polls. | backtest.py:180-196 vs live_trade.py:439-446 | verified | low-medium (BT conservative) |
| 13 | Friday cut-off and session use the bar OPEN time: an entry on the 15:55 bar (filled 16:00) is allowed; BT exits at the close of the 16:00 bar (16:05), LT at wall clock >= 16:00. | backtest.py:166,197; live_trade.py:571,627 | verified | low |
| 14 | Daily cap: BT `DayLimit` by bar date; LT counts broker deals of the wall-clock date (live_trade.py:241-257). Same intent. | — | verified | low |
| 15 | `find_bos_events` uses `if/elif`: an outside bar closing beyond both swings records only the bullish break. Doji (`close==open`) counts as bull: never a buy OB, always eligible as sell OB. | smc_core.py:533-538, 320-321 | verified | low |
| 16 | TR builds `Bos(swing_index=bi)` (wrong field) - unused downstream. | smc_core.py:1342 | verified | none |
| 17 | `amd_mode` with an unknown string: `day_amd` runs (slow) but nothing happens; `trend_filter` lowercase ("h1") raises KeyError. | smc_core.py:1365-1374, 451 | verified | low |
| 18 | Window/age coupling: if `ob_valid_bars` > ~`bos_max_age + 3k + 19`, OB and LQ pivots fall outside the `need` window; setups fail as "OB fell out of window" and LQ search is truncated. Sweeping ob_valid_bars alone is therefore biased; sweep bos_max_age >= ob_valid_bars. | smc_core.py:280-283, 1335-1337 | verified | medium for sweeps |

## 4. Fast engine vs backtest.run

| parameter / behaviour | engine | backtest.run | status |
|---|---|---|---|
| amd_mode="quality", amd_quality | not handled (`eligible` only "filter"; `quality()` has no AMD term; day cap uses quality without AMD) | +amd_quality, affects min_quality and good_quality | verified gap (engine.py:351-352, 330-339) |
| amd_accum_start/end, amd_manip_end, amd_reclaim, amd_min_range_pips | `amd_bias(m)` called with hard-coded defaults (120, 480, 600, 2.0, True) | Config values | verified gap (engine.py:294, 352; v2.py) |
| trend_ema | EMA fixed 50 (H1/H4), 20 (D1) | Config | verified gap (engine.py:109) |
| session_tz | hard-coded America/New_York | Config | verified gap (engine.py:40) |
| trade_weekends | `weekdays` fixed Mon-Fri, not mapped | Config | verified gap |
| second_open_start/end | not in Params, no bonus | scored | verified gap |
| fail_on_touch | candidates never fail | fails setups at touch | verified gap |
| close_at_session_end | `sess_start <= min < sess_end` (no overnight wrap) | `in_session` with wrap | verified gap (engine.py:495) |
| be_trigger_r = 0 / trail_start_r = 0 / friday_close = 00:00 | 0 means "off" | 0 is a real value | verified sentinel mismatch |
| pip_size | used in stage 1 but missing from STRUCT cache key | — | verified (stale cache if changed) |
| stops_level | Params default 0.05 price | CSV run: 0; MT5 run: broker value | verified, minor |
| spread / max_spread_pips | per-bar array = constant from `load` (0.30); no spread gate | constant `spread`, gate vs `max_spread_pips` | verified, minor |
| risk_percent, lot rounding, contract_size | R only, compounding `equity *= 1 + risk*R` | volume rounding to 0.01 lots | verified, minor |
| warm-up | `candidates_fast` only registers breaks at bars >= need | TR at first update registers breaks up to ob_valid_bars old | verified, edge only |
| entry_on_retouch, bos_max_age | ignored / only window | same (dead in TR) | consistent |
| pending cap 60, same-bar one signal, OB spent rule, busy until exit bar, Friday/BE/trail/gap fills | reproduced | — | consistent (check_engine.py covers shipped, H1 trend, trail, Friday, close-out) |
| extra in engine only | trend "against", d1, side, confirm "color", max_touch, ATR filters, atr_trail, time_stop, limit entry | not in bot | engine-only (results not live-tradable without code) |

## 5. Lifecycle and controlling parameters

| step | what happens | parameters |
|---|---|---|
| swing | fractal pivot, visible k bars later | swing_lookback |
| BOS | close beyond latest unbroken swing; window = required_bars | swing_lookback, bos_max_age (window only), ob_search_bars, lq_lookback_bars |
| OB | last opposite candle within search, impulse = break - OB | ob_search_bars, ob_pick, ob_use_body, ob_require_small_body, ob_max_body_ratio, max_impulse_bars |
| setup life | expires / dies on close through far edge / optional cancel | ob_valid_bars, cancel_on_opposite_break |
| impulse / pullback | bars since break >= max(min_pullback, impulse+extra) | min_pullback_bars, pullback_extra_bars, fail_on_touch |
| liquidity | equal lows/highs pool, swept before the touch | lq_tolerance_pips, lq_min_touches, lq_lookback_bars, lq_require_between_bos, lq_max_distance_pips, lq_sweep_within_bars, lq_allow_pre_ob |
| touch / entry confirmation | new visit to entry edge, in session, trend, close back outside, AMD, quality | session_start/end, session_tz, trade_weekends, trend_filter, trend_ema, entry_confirm_close, amd_*, quality_* + thresholds, min_quality (entry_on_retouch: no effect) |
| SL / TP | SL = stop edge -/+ buffer; TP = fill + rr x R; rails | sl_buffer_pips, sl_use_wick, rr, min_sl_pips, max_sl_pips, risk_percent, contract_size |
| BE / trail | +be_trigger_r -> entry + cost; step trail | be_trigger_r, be_cost_pips, trail_steps, trail_start_r, trail_gap_r |
| session / week exits | flat at session end (opt.), Friday cut-off | close_at_session_end, friday_close |
| daily limit | cap per NY day, extra for good setups | max_trades_per_day, extra_trades_if_quality, good_quality |
