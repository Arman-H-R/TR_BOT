# -*- coding: utf-8 -*-
"""
smc_core.py - the strategy. Nothing broker-specific, nothing about files.

Both live_trade.py and backtest.py import this module, so the logic you test is
exactly the logic that trades.

THE RULES (exactly as specified)
--------------------------------
1. BOS      any break of structure. BOS and CHOCH are both just "BOS": the
            first candle that CLOSES beyond an unbroken swing high / swing low.
2. OB       the candle that created the BOS: walking back from the break, the
            last candle whose colour is opposite to the impulse wave
            (buy setup -> last red candle), usually a small one.
3. SPEED    fast impulse, slow pullback. If the BOS was built in 3 candles the
            return to the OB must take at least 5 candles (and always at least
            impulse + pullback_extra_bars).
4. LQ       before price comes back to the OB there must be liquidity: two or
            more approximately equal lows (a "floor") / highs (a "ceiling")
            that price sweeps on its way to the OB.
            Example: buy OB at 4990, equal lows at 4999 / 5000 -> price takes
            the 4999/5000 floor, then touches 4990, then we buy.
5. ENTRY    a touch of the OB after that sweep. One entry per OB.
6. RISK     SL = far side of the OB -/+ sl_buffer_pips (4 pips).
            R  = |entry - SL|,  TP = entry +/- 4R (Config.rr).
            At +2R (be_trigger_r, v2; was 1R) the SL is moved to entry +/-
            be_cost_pips, so break-even is real after spread/commission ("tax").
7. FILTERS  XAUUSD only, New York session only.
8. v2       the touch candle must close back outside the OB (entry_confirm_close),
            flat on Friday from 16:00 New York (friday_close). Why: research/RESULTS.md

PIPS ON GOLD. A "pip" of XAUUSD is taken as 0.10 USD (Config.pip_size), so the
4-pip stop buffer = 0.40 USD and 1.00 lot (100 oz) risks 100 USD per 1.00 USD
of price. If your broker quotes gold differently, change Config.pip_size - it is
the only place that unit is defined.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time, timezone
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:  # tz database (needs the "tzdata" package on Windows)
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


# --------------------------------------------------------------------------- #
# 1. configuration - every number of the strategy lives here
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    # --- instrument ------------------------------------------------------
    symbol: str = "XAUUSD"        # gold only; live_trade.py refuses anything else
    timeframe: str = "M5"         # M1 M5 M15 M30 H1 ...
    pip_size: float = 0.10        # 1 pip of XAUUSD = 0.10 USD
    contract_size: float = 100.0  # 1.00 lot = 100 ounces

    # --- 1. structure / BOS ----------------------------------------------
    swing_lookback: int = 2       # fractal strength (bars each side of a pivot)
    bos_max_age: int = 80         # ignore a BOS older than this many bars

    # --- 2. order block ---------------------------------------------------
    ob_search_bars: int = 20      # how far back from the break to hunt the OB
    # An order block is always exactly ONE candle. But 42% of the time the BOS leg
    # starts with a RUN of opposite-coloured candles (26% are 2 long, 15% are 3+),
    # and then "the candle that created the BOS" is ambiguous. This picks which one
    # of the run to use - the zone stays a single candle either way.
    #   "last"    the one nearest the BOS  (the original behaviour)
    #   "first"   the one that started the run
    #   "extreme" lowest low for a buy / highest high for a sell
    #   "biggest" the widest high-low of the run
    ob_pick: str = "last"

    ob_use_body: bool = False     # False -> OB zone is the full candle range,
                                  # True  -> only open..close, so price has to come
                                  #          deeper into the candle before entry
    sl_use_wick: bool = True      # with ob_use_body, put the stop behind the WICK
                                  # instead of the body: a worse R but the wick is
                                  # where the candle actually stopped going
    ob_require_small_body: bool = False   # "little candle" as a hard rule
    ob_max_body_ratio: float = 1.0        # body(OB) <= ratio * mean impulse body
    ob_valid_bars: int = 80       # setup expires this many bars after the BOS.
                                  # Measured over 17 months: 40 -> 3.3 trades a
                                  # week at +0.067 R, 80 -> 4.2 a week at +0.081 R
                                  # once min_quality=1 filters the extra setups.
                                  # 120 and beyond only adds losers.

    # --- 3. fast impulse, slow pullback ----------------------------------
    # The rule is RELATIVE: the pullback must be slower than the impulse.
    # "BOS with 3 candles -> return with min 5 candles" is impulse + 2, which
    # pullback_extra_bars enforces on its own; the two absolute limits below are
    # only sanity rails and measurably change nothing (71 setups either way).
    max_impulse_bars: int = 20    # = ob_search_bars, i.e. effectively no cap
    min_pullback_bars: int = 3
    pullback_extra_bars: int = 2  # the actual rule: pullback >= impulse + 2

    # --- 5. entry ---------------------------------------------------------
    # "price go to LQ, touch OB then we trade it" - any touch counts, not only
    # the first candle to reach the zone. Measured on 65k M5 candles: first
    # touch only = 68 trades / +23.07 R, any re-touch = 103 trades / +26.37 R.
    # Still one entry per order block (the caller keys on Signal.ob_time).
    entry_on_retouch: bool = True

    # --- 5b. the touch decides (SetupTracker) ----------------------------
    # Your rule: watch the setup until price touches the OB. At that touch it is
    # either valid and we trade, or it is not and the SETUP FAILS - no second
    # chance. Measured on XAUUSD M5 (18 Sep - 23 Oct 2026): True gives 0 trades,
    # because gold usually snaps back into the block 1-4 candles after the break,
    # which rule 3 calls "too fast". False keeps the setup alive: that touch is
    # wasted, and if price leaves the zone and returns later - slowly - it can
    # still fire. Same window: 24 trades.
    fail_on_touch: bool = False

    # Not one of your seven rules, so it is OFF: when True, a break in the other
    # direction retires every setup pointing the old way. On M5 with
    # swing_lookback=2 breaks alternate constantly, so switching this on killed
    # 560 of 675 setups in five weeks and left zero trades.
    cancel_on_opposite_break: bool = False

    # --- 4. liquidity -----------------------------------------------------
    # "Approximately equal" calibrated from the spec's own example: a floor of
    # 4999 and 5000 is one pool, i.e. 1.00 USD = 10 pips apart. Tightening this
    # to 5 pips would reject that example; it would also have made more money
    # here (108 trades / +29.52 R against 154 trades / +16.07 R), so the extra
    # setups this width allows are the weaker ones. Your rule, your call.
    lq_tolerance_pips: float = 10.0
    lq_min_touches: int = 2          # two equal lows / highs are enough
    lq_lookback_bars: int = 80       # window searched around / before the OB.
                                     # Swept over 44 weeks: 30-80 is a plateau
                                     # (+0.14..+0.19R), >100 decays (+0.04R at 160).
                                     # 80 and 40 tie on return (+0.80 vs +0.79 R/week)
                                     # but 80 makes 4+ trades in 84% of weeks against
                                     # 63% for 40, so it holds the >=4/week rule.
                                     # 120 (the old value) earned +0.61 R/week.
    # Where the pool is allowed to sit.
    #   True  - only between the OB and the level the BOS broke (the old rule)
    #   False - anywhere price had to pass through to reach the OB, which is what
    #           "price go to LQ, touch OB then we trade it" actually describes.
    # The narrow rule throws away 663 pools in five months, including the kind
    # drawn on your chart, where the equal floors sit ABOVE the swing the BOS
    # broke. Your reading is right about the chart - but measured over 44 weeks
    # on the same candles, trading those extra pools LOSES the edge:
    #     narrow (True) : 5.4 trades/week, +0.108 R, positive in both halves
    #     wide  (False) : 4.5 trades/week, +0.036 R, newer half negative
    # So it ships narrow. Set it to False to trade the chart pattern as well, and
    # re-run the backtest before you trust it.
    lq_require_between_bos: bool = True
    # Two limits that keep "anywhere above the OB" honest, taken from what your
    # chart actually shows: the pool sits CLOSE to the order block, and price
    # sweeps it just before it reaches the block.
    lq_max_distance_pips: float = 0.0   # 0 = no limit
    lq_sweep_within_bars: int = 0       # 0 = no limit
    # True also accepts the classic "sweep -> OB -> BOS" pool, swept BEFORE the
    # OB printed. That is not what rule 4 describes, and on 65k M5 candles those
    # extra 39 trades returned -0.55 R in total, so it is off.
    lq_allow_pre_ob: bool = False

    # --- 6. risk ----------------------------------------------------------
    sl_buffer_pips: float = 4.0   # SL sits 4 pips beyond the OB
    rr: float = 4.0               # TP = 4 x risk. Swept over 44 weeks with the
                                  # break-even rule on: 2R +0.081, 3R +0.155,
                                  # 4R +0.244, 5R +0.271, 6R +0.177 R per trade.
                                  # 4R is picked over the 5R peak because it rests
                                  # on 40 winners instead of 33 at the same 12.7R
                                  # drawdown. The gap to 3R is ~1 standard error,
                                  # so this is a lean, not a proven improvement.
    be_trigger_r: float = 2.0     # move to break-even at +2R (v2; was 1R). 1R moved
                                  # the stop too early: 1.5-2R was better in both the
                                  # in-sample and the out-of-sample half (research/RESULTS.md)
    be_cost_pips: float = 2.0     # ... entry + 2 pips, to cover the "tax"
    # Step trailing after break-even: once price reaches +kR (k >= trail_start_r)
    # the stop moves to +(k - trail_gap_r)R. With the defaults and rr=4:
    #   +1R -> SL to entry + tax (break-even, above)
    #   +2R -> SL to +1R        +3R -> SL to +2R        +4R -> TP
    # Off by default; live_trade.py does not trail yet.
    trail_steps: bool = False
    trail_start_r: float = 2.0
    trail_gap_r: float = 1.0
    min_sl_pips: float = 4.0      # the spec's own floor: the 4-pip buffer
    max_sl_pips: float = 150.0    # a rail of mine: 15 USD of risk is plenty
    risk_percent: float = 0.5     # % of equity risked per trade

    # --- 7. session (New York only) --------------------------------------
    session_tz: str = "America/New_York"
    session_start: time = time(8, 0)
    session_end: time = time(17, 0)
    utc_start: time = time(13, 0)  # fallback window if tzdata is missing
    utc_end: time = time(21, 0)
    trade_weekends: bool = False
    close_at_session_end: bool = False  # True -> flatten when NY closes

    # --- 2b. what makes an order block GOOD -------------------------------
    # Your rule 2 says a good OB is the small candle that created the BOS. These
    # weights turn that into a number, so "good" is measurable instead of a
    # feeling. Each part was checked on 244 trades, split into the older 60% and
    # the newer 40% of the history; only parts that were positive in BOTH halves
    # are given weight here.
    #
    #   small_ob     OB height <= ob_small_pips      old +0.19  new +0.28  (48)
    #   lq_strong    liquidity pool has 3+ touches    old +0.18  new +0.49  (48)
    #   sharp        the impulse took <= 2 candles    old +0.09  new +0.44  (106)
    #   slow_return  pullback >= 8x the impulse       old +0.21  new +0.13  (97)
    #   first_hour   entry in the first NY hour       old +0.52  new +0.52  (58)
    #
    # first_hour is the steadiest thing in the whole data set; it is also the
    # most expensive filter, because it only fires once a day.
    quality_small_ob: int = 2
    quality_lq_strong: int = 2
    quality_sharp_impulse: int = 1
    quality_slow_return: int = 1
    quality_first_hour: int = 2
    ob_small_pips: float = 20.0       # "little candle" in pips of OB height
    lq_strong_touches: int = 3
    sharp_impulse_bars: int = 2
    slow_return_ratio: float = 8.0
    first_hour_start: int = 8         # NY hour the "first hour" bonus starts at.
                                      # Kept separate from session_start so widening
                                      # the session (e.g. to include London) does not
                                      # silently stretch a 1-hour bonus over 6 hours.
    first_hour_end: int = 9           # NY hour at which the first hour ends
    # Optional second open to reward, e.g. the London open at 03:00 NY.
    # -1 disables it.
    second_open_start: int = -1
    second_open_end: int = -1
    min_quality: int = 1              # 0 = take every valid setup. 1 keeps roughly
                                      # four trades a week; 4-5 is the only range
                                      # that made money in BOTH halves of the
                                      # history, at about one trade a week.

    # --- 8. AMD: accumulation, manipulation, distribution -----------------
    # The day builds a range before the New York open (accumulation), the open
    # pushes through one side of it to take the stops there (manipulation), and
    # the real move goes the other way (distribution). If that is true, a setup
    # pointing AGAINST the manipulation is the one to take.
    #   amd_mode = "off"     ignore it
    #              "filter"  only trade in the distribution direction
    #              "quality" no veto, just +amd_quality points when it agrees
    amd_mode: str = "off"
    amd_accum_start: time = time(2, 0)    # local to session_tz: the range builds
    amd_accum_end: time = time(8, 0)
    amd_manip_end: time = time(10, 0)     # the sweep must happen before this
    amd_reclaim: bool = True              # price must close back inside the range
    amd_min_range_pips: float = 20.0      # ignore a range too small to mean anything
    amd_quality: int = 2                  # weight when amd_mode = "quality"

    # --- 9. filters and exits added by the v2 research (research/RESULTS.md) --
    # Higher-timeframe trend: only take an order block that points the same way
    # as the last CLOSED candle of trend_tf relative to its EMA(trend_ema).
    #   "off" | "H1" | "H4"
    trend_filter: str = "off"
    trend_ema: int = 50
    # The touch candle has to CLOSE back outside the zone (a rejection), i.e.
    # above the OB top for a buy, below the OB bottom for a sell.
    entry_confirm_close: bool = True       # v2 default
    # Fridays: no new entry from this New York time on, and an open position is
    # closed at it - weekend gaps jump straight through stops. None = off.
    friday_close: Optional[time] = time(16, 0)   # v2 default

    # --- housekeeping -----------------------------------------------------
    max_trades_per_day: int = 3
    extra_trades_if_quality: int = 2  # a setup scoring >= good_quality may use
                                      # this many slots beyond max_trades_per_day
    good_quality: int = 5             # "a good setup can trade more" 

    # derived --------------------------------------------------------------
    def pips(self, n: float) -> float:
        """pips -> price distance"""
        return n * self.pip_size

    def to_pips(self, price_distance: float) -> float:
        """price distance -> pips"""
        return price_distance / self.pip_size

    def required_bars(self) -> int:
        """How much history find_signal() needs in order to see one setup."""
        return (self.bos_max_age + self.ob_search_bars + self.lq_lookback_bars
                + 4 * self.swing_lookback + 20)


# --------------------------------------------------------------------------- #
# 2. candles
# --------------------------------------------------------------------------- #
@dataclass
class Bars:
    """Closed candles as plain numpy arrays (cheap to slice, no look-ahead)."""

    time: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> "Bars":
        df = normalize_frame(df)
        return cls(
            time=df.index,
            open=df["open"].to_numpy(float),
            high=df["high"].to_numpy(float),
            low=df["low"].to_numpy(float),
            close=df["close"].to_numpy(float),
        )

    def __len__(self) -> int:
        return len(self.close)

    def window(self, start: int, end: int) -> "Bars":
        """bars[start:end] - numpy views, no copy."""
        start = max(0, start)
        return Bars(self.time[start:end], self.open[start:end],
                    self.high[start:end], self.low[start:end],
                    self.close[start:end])

    def bull(self, i: int) -> bool:
        return self.close[i] >= self.open[i]

    def body(self, i: int) -> float:
        return abs(self.close[i] - self.open[i])


def normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """time,open,high,low,close in any case / naming -> UTC-indexed OHLC frame."""
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    out = out.rename(columns={"date": "time", "datetime": "time",
                              "timestamp": "time", "o": "open", "h": "high",
                              "l": "low", "c": "close", "price": "close",
                              "tick_volume": "volume"})

    if not isinstance(out.index, pd.DatetimeIndex):
        if "time" not in out.columns:
            raise ValueError("data needs a time/date column or a DatetimeIndex")
        out["time"] = pd.to_datetime(out["time"], utc=True, errors="coerce")
        out = out.dropna(subset=["time"]).set_index("time")

    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")

    missing = {"open", "high", "low", "close"} - set(out.columns)
    if missing:
        raise ValueError("data is missing columns: %s" % sorted(missing))

    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.dropna(subset=["open", "high", "low", "close"])


# --------------------------------------------------------------------------- #
# 3. session filter (rule 7)
# --------------------------------------------------------------------------- #
def _tz(cfg: Config):
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(cfg.session_tz)
    except Exception:
        return None


def session_timezone(cfg: Config):
    """The session timezone, or UTC when no tz database is installed."""
    return _tz(cfg) or timezone.utc


def session_window(ts, cfg: Config):
    """(start, end) in UTC of the window around `ts`, or the next one after it.

    Handy for answering "when will this thing actually trade?" - DST included,
    weekends skipped. Returns (None, None) if nothing is found within a week.
    """
    tz = session_timezone(cfg)
    ts = pd.Timestamp(ts)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    local = ts.tz_convert(tz)

    for add in range(8):
        day = (local + pd.Timedelta(days=add)).date()
        start = pd.Timestamp.combine(day, cfg.session_start).tz_localize(tz)
        end = pd.Timestamp.combine(day, cfg.session_end).tz_localize(tz)
        if not cfg.trade_weekends and start.weekday() >= 5:
            continue
        if local < end:
            return start.tz_convert("UTC"), end.tz_convert("UTC")
    return None, None


def in_session(ts, cfg: Config) -> bool:
    """True inside the New York window (DST handled when tzdata is available)."""
    ts = pd.Timestamp(ts)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    tz = _tz(cfg)
    if tz is not None:
        local = ts.tz_convert(tz)
        start, end = cfg.session_start, cfg.session_end
    else:  # no tz database -> fixed UTC window
        local, start, end = ts, cfg.utc_start, cfg.utc_end

    if not cfg.trade_weekends and local.weekday() >= 5:
        return False
    t = local.time()
    return start <= t < end if start <= end else (t >= start or t < end)


def friday_cutoff(ts, cfg: Config, entry_ts=None) -> bool:
    """True from cfg.friday_close (NY time) on a Friday until the week ends.

    With entry_ts it is also True when the position has been carried over a
    weekend (the bot was down, or a holiday closed the market early on Friday),
    so the first chance after the gap is used to get out.
    """
    if cfg.friday_close is None:
        return False
    tz = session_timezone(cfg)
    ts = pd.Timestamp(ts)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
    local = ts.tz_convert(tz)
    if local.weekday() >= 5 or (local.weekday() == 4 and local.time() >= cfg.friday_close):
        return True
    if entry_ts is not None:
        e = pd.Timestamp(entry_ts)
        e = e.tz_localize("UTC") if e.tzinfo is None else e
        if (local.normalize() - e.tz_convert(tz).normalize()).days >= 3 and local.weekday() < e.tz_convert(tz).weekday():
            return True
    return False


# --------------------------------------------------------------------------- #
# 3b. higher-timeframe trend (filter 9)
# --------------------------------------------------------------------------- #
TREND_RULES = {"H1": "1h", "H4": "4h"}


def trend_series(index: pd.DatetimeIndex, close: np.ndarray, cfg: Config) -> np.ndarray:
    """+1 / -1 for every candle: the last CLOSED higher-timeframe close against
    its EMA(trend_ema), without look-ahead.

    The higher-timeframe candle opening at T is usable from T + its length,
    i.e. by the first candle that OPENS after it closed. The live bot does the
    same (live_trade.htf_trend()).
    """
    if cfg.trend_filter == "off":
        return np.zeros(len(index), int)
    rule = TREND_RULES[cfg.trend_filter]
    s = pd.Series(np.asarray(close, float), index=index)
    htf = s.resample(rule, label="left", closed="left").last().dropna()
    return trend_from_htf(htf.index, htf.to_numpy(), index, cfg)


def trend_from_htf(htf_time, htf_close, index, cfg: Config) -> np.ndarray:
    """Trend for each time in `index` from higher-timeframe candles (open
    times + closes). Shared by the backtest (candles resampled from M5) and the
    live bot (the broker's own H1/H4 candles)."""
    rule = TREND_RULES[cfg.trend_filter]
    htf = pd.Series(np.asarray(htf_close, float), index=pd.DatetimeIndex(htf_time))
    ema = htf.ewm(span=cfg.trend_ema, adjust=False).mean()
    sign = np.sign(htf - ema)
    usable = pd.Series(sign.values, index=sign.index + pd.Timedelta(rule))
    usable = usable[~usable.index.duplicated(keep="last")]
    return usable.reindex(pd.DatetimeIndex(index), method="ffill").fillna(0).astype(int).to_numpy()


# --------------------------------------------------------------------------- #
# 4. swings and BOS (rule 1)
# --------------------------------------------------------------------------- #
Pivot = Tuple[int, float]  # (bar index, price)


def find_swings(bars: Bars, k: int) -> Tuple[List[Pivot], List[Pivot]]:
    """Fractal pivots: a high with k lower highs on both sides (mirror for lows).

    A pivot is only knowable k bars after it printed - callers respect that.
    """
    h, l = bars.high, bars.low
    n = len(h)
    if k < 1 or n < 2 * k + 1:
        return [], []

    try:
        win = np.lib.stride_tricks.sliding_window_view
        kmax = win(h, k).max(axis=1)      # kmax[j] = max(h[j:j+k])
        kmin = win(l, k).min(axis=1)
        c = np.arange(k, n - k)
        hi_mask = (h[c] > kmax[c - k]) & (h[c] >= kmax[c + 1])
        lo_mask = (l[c] < kmin[c - k]) & (l[c] <= kmin[c + 1])
        highs = [(int(i), float(h[i])) for i in c[hi_mask]]
        lows = [(int(i), float(l[i])) for i in c[lo_mask]]
        return highs, lows
    except Exception:  # pragma: no cover - very old numpy
        highs, lows = [], []
        for i in range(k, n - k):
            if h[i] > h[i - k:i].max() and h[i] >= h[i + 1:i + k + 1].max():
                highs.append((i, float(h[i])))
            if l[i] < l[i - k:i].min() and l[i] <= l[i + 1:i + k + 1].min():
                lows.append((i, float(l[i])))
        return highs, lows


@dataclass
class Bos:
    direction: int      # +1 bullish, -1 bearish
    break_index: int    # the candle that closed beyond the swing
    level: float        # the swing level that was taken
    swing_index: int


def find_bos_events(bars: Bars, highs: Sequence[Pivot], lows: Sequence[Pivot],
                    cfg: Config) -> List[Bos]:
    """Every structure break, oldest first. BOS and CHOCH are both breaks."""
    k = cfg.swing_lookback
    events: List[Bos] = []
    hi = li = 0
    last_high: Optional[Pivot] = None
    last_low: Optional[Pivot] = None

    for i in range(len(bars)):
        # a pivot becomes visible only k bars after it printed
        while hi < len(highs) and highs[hi][0] + k <= i:
            last_high = highs[hi]
            hi += 1
        while li < len(lows) and lows[li][0] + k <= i:
            last_low = lows[li]
            li += 1

        c = bars.close[i]
        if last_high is not None and c > last_high[1]:
            events.append(Bos(+1, i, last_high[1], last_high[0]))
            last_high = None          # each swing can be broken only once
        elif last_low is not None and c < last_low[1]:
            events.append(Bos(-1, i, last_low[1], last_low[0]))
            last_low = None
    return events


# --------------------------------------------------------------------------- #
# 5. order block (rule 2)
# --------------------------------------------------------------------------- #
@dataclass
class OrderBlock:
    direction: int
    index: int
    top: float
    bottom: float
    impulse_bars: int
    wick_top: float = 0.0         # the full candle range, kept even when the zone
    wick_bottom: float = 0.0      # is the body, so the stop can sit behind the wick

    @property
    def entry_edge(self) -> float:
        """The side price must touch: OB high for a buy, OB low for a sell."""
        return self.top if self.direction > 0 else self.bottom

    @property
    def far_edge(self) -> float:
        """The side the stop goes behind."""
        return self.bottom if self.direction > 0 else self.top

    def stop_edge(self, cfg: "Config") -> float:
        """far_edge, or the wick beyond it when sl_use_wick is on."""
        if not (cfg.ob_use_body and cfg.sl_use_wick):
            return self.far_edge
        if self.direction > 0:
            return min(self.far_edge, self.wick_bottom or self.far_edge)
        return max(self.far_edge, self.wick_top or self.far_edge)


def find_order_block(bars: Bars, bos: Bos, cfg: Config) -> Optional[OrderBlock]:
    """The candle that created the BOS: last opposite-coloured candle of the leg."""
    d, b = bos.direction, bos.break_index
    floor_i = max(0, b - cfg.ob_search_bars)

    def is_opposite(j: int) -> bool:
        return (not bars.bull(j)) if d > 0 else bars.bull(j)

    idx = None
    for j in range(b - 1, floor_i - 1, -1):
        if is_opposite(j):
            idx = j
            break

    if idx is not None and cfg.ob_pick != "last":
        # walk back over the whole run of opposite candles, then pick one of them
        run_end = idx
        run_start = idx
        while run_start - 1 >= floor_i and is_opposite(run_start - 1):
            run_start -= 1
        run = list(range(run_start, run_end + 1))
        if cfg.ob_pick == "first":
            idx = run[0]
        elif cfg.ob_pick == "extreme":
            idx = (min(run, key=lambda j: bars.low[j]) if d > 0
                   else max(run, key=lambda j: bars.high[j]))
        elif cfg.ob_pick == "biggest":
            idx = max(run, key=lambda j: bars.high[j] - bars.low[j])
        else:
            raise ValueError(f"unknown ob_pick: {cfg.ob_pick!r}")

    if idx is None:                      # no opposite candle -> use the leg origin
        if b - floor_i < 2:
            return None
        rel = (int(np.argmin(bars.low[floor_i:b + 1])) if d > 0
               else int(np.argmax(bars.high[floor_i:b + 1])))
        idx = floor_i + rel
        if idx >= b:
            return None

    impulse = b - idx                    # candles used to build the BOS
    if impulse < 1 or impulse > cfg.max_impulse_bars:
        return None

    if cfg.ob_require_small_body:
        bodies = [bars.body(j) for j in range(idx + 1, b + 1)]
        mean_body = float(np.mean(bodies)) if bodies else 0.0
        if mean_body > 0 and bars.body(idx) > cfg.ob_max_body_ratio * mean_body:
            return None

    wick_top, wick_bottom = float(bars.high[idx]), float(bars.low[idx])
    if cfg.ob_use_body:
        top = float(max(bars.open[idx], bars.close[idx]))
        bottom = float(min(bars.open[idx], bars.close[idx]))
    else:
        top, bottom = wick_top, wick_bottom
    if top <= bottom:
        return None
    return OrderBlock(d, idx, top, bottom, impulse,
                      wick_top=wick_top, wick_bottom=wick_bottom)


# --------------------------------------------------------------------------- #
# 6. liquidity (rule 4)
# --------------------------------------------------------------------------- #
@dataclass
class Liquidity:
    level: float
    touches: int
    last_index: int
    where: str          # "pullback" (between OB and BOS) or "pre_ob"


def _clusters(pivots: Sequence[Pivot], lo: int, hi: int, tol: float,
              min_touches: int) -> List[Tuple[float, List[Pivot]]]:
    """Group pivots whose prices are within tol -> equal highs / equal lows."""
    pool = [p for p in pivots if lo <= p[0] <= hi]
    out: List[Tuple[float, List[Pivot]]] = []
    for anchor in pool:
        group = [p for p in pool if abs(p[1] - anchor[1]) <= tol]
        if len(group) >= min_touches:
            level = float(np.mean([p[1] for p in group]))
            if not any(abs(level - lv) <= tol / 2 for lv, _ in out):
                out.append((level, group))
    return out


def c_to_pips(cfg: Config, price_distance: float) -> float:
    return cfg.to_pips(price_distance)


def find_liquidity(bars: Bars, highs: Sequence[Pivot], lows: Sequence[Pivot],
                   ob: OrderBlock, bos: Bos, upto: int,
                   cfg: Config) -> Optional[Liquidity]:
    """Equal lows above a buy OB (or equal highs below a sell OB) that price swept.

    A pool is two or more swing lows (for a buy) or highs (for a sell) sitting
    within lq_tolerance_pips of each other - "approximately equal floors", which
    may be several slightly different levels that together form one band.

    buy  : equal lows ABOVE the OB that price broke through on its way down
    sell : equal highs BELOW the OB that price broke through on its way up
    With lq_require_between_bos the pool must also sit below (above) the level the
    BOS broke, which is the stricter reading of rule 4.
    optional (lq_allow_pre_ob): a pool swept BEFORE the OB printed, i.e. the
    classic sweep -> OB -> BOS sequence.
    """
    tol = cfg.pips(cfg.lq_tolerance_pips)
    lo = max(0, ob.index - cfg.lq_lookback_bars)
    pivots = lows if ob.direction > 0 else highs
    found: List[Liquidity] = []

    for level, group in _clusters(pivots, lo, upto, tol, cfg.lq_min_touches):
        last = max(p[0] for p in group)
        if last >= upto:                       # must be swept, not just printed
            continue

        if ob.direction > 0:
            between = ob.top < level <= bos.level
            beyond = level > ob.top                 # anywhere above the order block
            pre = level <= ob.bottom
        else:
            between = bos.level <= level < ob.bottom
            beyond = level < ob.bottom
            pre = level >= ob.top

        on_the_way = between if cfg.lq_require_between_bos else beyond
        if on_the_way:
            if cfg.lq_max_distance_pips > 0:
                if c_to_pips(cfg, abs(level - ob.entry_edge)) > cfg.lq_max_distance_pips:
                    continue
            # price has to have gone THROUGH the pool, not merely past its level:
            # find the first candle after the pool formed that traded beyond it
            swept_at = None
            for j in range(last + 1, upto + 1):
                if (bars.low[j] < level) if ob.direction > 0 else (bars.high[j] > level):
                    swept_at = j
                    break
            if swept_at is None:
                continue
            if cfg.lq_sweep_within_bars > 0 and (upto - swept_at) > cfg.lq_sweep_within_bars:
                continue                      # the sweep is old news, not this move
            found.append(Liquidity(level, len(group), last, "pullback"))
        elif pre and cfg.lq_allow_pre_ob and last < ob.index:
            seg = slice(last + 1, min(ob.index + 2, upto + 1))
            swept = (float(bars.low[seg].min()) < level - tol if ob.direction > 0
                     else float(bars.high[seg].max()) > level + tol)
            if swept:
                found.append(Liquidity(level, len(group), last, "pre_ob"))

    if not found:
        return None
    ref = ob.entry_edge                        # the pool nearest the OB matters
    found.sort(key=lambda z: abs(z.level - ref))
    return found[0]


# --------------------------------------------------------------------------- #
# 7. the signal (rules 3 + 5)
# --------------------------------------------------------------------------- #
@dataclass
class Signal:
    time: pd.Timestamp
    direction: int          # +1 buy, -1 sell
    touch_price: float      # the OB edge that was touched
    sl: float               # from the OB, not from the fill
    ob: OrderBlock
    bos: Bos
    lq: Liquidity
    impulse_bars: int
    pullback_bars: int
    ob_time: Optional[pd.Timestamp] = None   # identifies the OB across windows
    quality: int = 0                         # see ob_quality()
    quality_parts: dict = field(default_factory=dict)

    @property
    def side(self) -> str:
        return "BUY" if self.direction > 0 else "SELL"

    def describe(self, cfg: Config) -> str:
        tags = "+".join(self.quality_parts) or "none"
        return ("%s %s @~%.2f SL %.2f | quality %d/%d (%s) | BOS %.2f in %d bars, "
                "pullback %d bars, OB %.2f-%.2f, LQ %.2f x%d (%s)"
                % (self.side, cfg.symbol, self.touch_price, self.sl,
                   self.quality, max_quality(cfg), tags,
                   self.bos.level, self.impulse_bars, self.pullback_bars,
                   self.ob.bottom, self.ob.top, self.lq.level,
                   self.lq.touches, self.lq.where))


def find_signal(bars: Bars, cfg: Config,
                require_session: bool = True) -> Optional[Signal]:
    """Evaluate the LAST closed candle of `bars`. Returns a Signal or None.

    Only closed candles are read, so the answer for bar i never depends on
    bar i+1 - the backtest and the live bot see exactly the same thing.

    Recent breaks are tried newest first. The newest one alone is not enough:
    taking the LQ pool (rule 4) usually closes beyond a minor swing of its own,
    which is itself a break in the opposite direction - the setup we want is the
    older break whose order block price has just come back to.
    """
    n = len(bars)
    if n < 2 * cfg.swing_lookback + 10:
        return None
    i = n - 1

    if require_session and not in_session(bars.time[i], cfg):
        return None

    highs, lows = find_swings(bars, cfg.swing_lookback)
    events = find_bos_events(bars, highs, lows, cfg)
    max_age = min(cfg.bos_max_age, cfg.ob_valid_bars)

    for bos in reversed(events):                      # rule 1
        age = i - bos.break_index
        if age <= 0:
            continue
        if age > max_age:
            break                                     # everything older is worse
        signal = _setup_from_bos(bars, highs, lows, bos, i, cfg)
        if signal is not None:
            return signal
    return None


def _setup_from_bos(bars: Bars, highs: Sequence[Pivot], lows: Sequence[Pivot],
                    bos: Bos, i: int, cfg: Config) -> Optional[Signal]:
    """Rules 2-5 for one structure break, evaluated at candle i."""
    ob = find_order_block(bars, bos, cfg)             # rule 2
    if ob is None:
        return None

    # rule 3: fast impulse, slow pullback
    pullback = i - bos.break_index
    if pullback < max(cfg.min_pullback_bars,
                      ob.impulse_bars + cfg.pullback_extra_bars):
        return None

    d = ob.direction
    edge = ob.entry_edge

    # the OB must still be alive: nothing CLOSED through it since the break
    seg = slice(bos.break_index + 1, i + 1)
    if d > 0 and float(bars.close[seg].min()) < ob.bottom:
        return None
    if d < 0 and float(bars.close[seg].max()) > ob.top:
        return None

    # rule 5: price touches the OB.
    # entry_on_retouch=False: only the very first candle that reaches the zone
    #   may fire, so a setup whose LQ or pullback count was not ready yet at
    #   that moment is lost for good.
    # entry_on_retouch=True: any candle that comes back INTO the zone from
    #   outside may fire; the caller keeps one entry per OB (Signal.ob_time).
    before = slice(bos.break_index + 1, i)
    has_before = before.stop > before.start
    if d > 0:
        touched_now = bars.low[i] <= edge
        touched_before = has_before and float(bars.low[before].min()) <= edge
        came_from_outside = bars.low[i - 1] > edge
    else:
        touched_now = bars.high[i] >= edge
        touched_before = has_before and float(bars.high[before].max()) >= edge
        came_from_outside = bars.high[i - 1] < edge
    if not touched_now:
        return None
    if cfg.entry_on_retouch:
        if touched_before and not came_from_outside:
            return None                # still sitting inside the zone
    elif touched_before:
        return None

    lq = find_liquidity(bars, highs, lows, ob, bos, i, cfg)   # rule 4
    if lq is None:
        return None

    stop_ref = ob.stop_edge(cfg)
    sl = (stop_ref - cfg.pips(cfg.sl_buffer_pips) if d > 0
          else stop_ref + cfg.pips(cfg.sl_buffer_pips))

    return Signal(time=pd.Timestamp(bars.time[i]), direction=d,
                  touch_price=float(edge), sl=float(sl), ob=ob, bos=bos, lq=lq,
                  impulse_bars=ob.impulse_bars, pullback_bars=pullback,
                  ob_time=pd.Timestamp(bars.time[ob.index]))


def state_line(bars: Bars, cfg: Config) -> str:
    """One line on what the detector currently sees - for watching it run.

    A signal is rare, so without this a session of dry-running is just silence.
    """
    n = len(bars)
    if n < 2 * cfg.swing_lookback + 10:
        return "not enough candles yet"
    i = n - 1

    highs, lows = find_swings(bars, cfg.swing_lookback)
    events = find_bos_events(bars, highs, lows, cfg)
    if not events:
        return "no structure break in range"

    # the same candidates find_signal() considers, ranked by how close each is
    # to firing - describing only the newest break would point at the wrong one
    best = None
    alive = 0
    max_age = min(cfg.bos_max_age, cfg.ob_valid_bars)
    for bos in reversed(events):
        age = i - bos.break_index
        if age <= 0:
            continue
        if age > max_age:
            break
        ob = find_order_block(bars, bos, cfg)
        if ob is None:
            continue

        d, edge = ob.direction, ob.entry_edge
        since = slice(bos.break_index + 1, i + 1)
        before = slice(bos.break_index + 1, i)
        has_before = before.stop > before.start
        if d > 0:
            if float(bars.close[since].min()) < ob.bottom:
                continue                          # OB closed through: dead
            tagged = has_before and float(bars.low[before].min()) <= edge
            gap = float(bars.low[i]) - edge
        else:
            if float(bars.close[since].max()) > ob.top:
                continue
            tagged = has_before and float(bars.high[before].max()) >= edge
            gap = edge - float(bars.high[i])

        alive += 1
        need = max(cfg.min_pullback_bars, ob.impulse_bars + cfg.pullback_extra_bars)
        has_lq = find_liquidity(bars, highs, lows, ob, bos, i, cfg) is not None
        rank = (not tagged, age >= need, has_lq, -max(gap, 0.0))
        if best is None or rank > best[0]:
            best = (rank, bos, ob, age, need, gap, has_lq, tagged)

    if best is None:
        return "no live order block"

    _, bos, ob, age, need, gap, has_lq, tagged = best
    parts = ["%s OB %.2f-%.2f (BOS %.2f, %db ago)"
             % ("buy" if ob.direction > 0 else "sell", ob.bottom, ob.top,
                bos.level, age)]
    if tagged:
        parts.append("already tagged, spent")
    else:
        parts.append("AT the OB" if gap <= 0 else "touch in %.2f" % gap)
        if age < need:
            parts.append("pullback %d/%d bars" % (age, need))
        if not has_lq:
            parts.append("no LQ yet")
    if alive > 1:
        parts.append("+%d more" % (alive - 1))
    return " | ".join(parts)


# --------------------------------------------------------------------------- #
# 8. the trade: SL / TP / break-even (rule 6)
# --------------------------------------------------------------------------- #
@dataclass
class Trade:
    direction: int
    entry: float
    sl: float
    tp: float
    risk: float          # price distance entry -> initial SL
    be_trigger: float    # price that arms the break-even move (+1R)
    be_price: float      # entry + "tax"
    initial_sl: float
    be_done: bool = False
    trail_r: float = 0.0  # R the stop is locked at by the step trail (0 = none)

    def r_multiple(self, price: float) -> float:
        if not self.risk:
            return 0.0
        return (price - self.entry) * self.direction / self.risk


def build_trade(signal: Signal, fill: float, cfg: Config) -> Optional[Trade]:
    """R and TP come from the REAL fill, the stop comes from the order block.

    entry 5000, sl 4990 -> R = 10 -> TP = 5020, break-even armed at 5010.
    """
    d = signal.direction
    sl = signal.sl
    risk = (fill - sl) * d
    if risk <= 0:
        return None

    sl_pips = cfg.to_pips(risk)
    if not (cfg.min_sl_pips <= sl_pips <= cfg.max_sl_pips):
        return None

    return Trade(direction=d, entry=fill, sl=sl, initial_sl=sl,
                 tp=fill + d * cfg.rr * risk, risk=risk,
                 be_trigger=fill + d * cfg.be_trigger_r * risk,
                 be_price=fill + d * cfg.pips(cfg.be_cost_pips))


def apply_breakeven(trade: Trade, high: float, low: float) -> bool:
    """At +1R move the stop to entry + cost. Returns True if the SL moved."""
    if trade.be_done:
        return False
    if trade.direction > 0:
        if high >= trade.be_trigger and trade.be_price > trade.sl:
            trade.sl = trade.be_price
            trade.be_done = True
            return True
    else:
        if low <= trade.be_trigger and trade.be_price < trade.sl:
            trade.sl = trade.be_price
            trade.be_done = True
            return True
    return False


def apply_trailing(trade: Trade, high: float, low: float, cfg: Config) -> bool:
    """Step trail (cfg.trail_steps): at +kR the stop goes to +(k - gap)R.

    Uses the best price of the candle, so call it AFTER checking whether the
    candle hit the old stop - the same order apply_breakeven() is used in.
    Returns True if the SL moved.
    """
    if not cfg.trail_steps or not trade.risk:
        return False
    best = high if trade.direction > 0 else low
    reached = trade.r_multiple(best)
    if reached < cfg.trail_start_r:
        return False
    k = cfg.trail_start_r + int((reached - cfg.trail_start_r) // 1.0)   # whole R steps
    lock = k - cfg.trail_gap_r
    if lock <= trade.trail_r:
        return False
    new_sl = trade.entry + trade.direction * lock * trade.risk
    if (new_sl - trade.sl) * trade.direction <= 0:
        return False
    trade.sl, trade.trail_r, trade.be_done = new_sl, lock, True
    return True


def lot_size(equity: float, trade_risk_price: float, cfg: Config,
             volume_min: float = 0.01, volume_max: float = 100.0,
             volume_step: float = 0.01) -> float:
    """Volume that risks risk_percent of equity over the stop distance."""
    if trade_risk_price <= 0 or equity <= 0:
        return volume_min
    money = equity * cfg.risk_percent / 100.0
    raw = money / (trade_risk_price * cfg.contract_size)
    steps = int(raw / volume_step)                 # round DOWN, never over-risk
    vol = max(volume_min, min(volume_max, steps * volume_step))
    return round(vol, 2)


@dataclass
class Amd:
    """What the day's accumulation / manipulation phase says."""
    bias: int                 # +1 expect up, -1 expect down, 0 nothing yet
    range_low: float
    range_high: float
    swept: str                # "high", "low", "both" or ""
    reclaimed: bool

    @property
    def size(self) -> float:
        return self.range_high - self.range_low


def day_amd(bars: Bars, i: int, cfg: Config) -> Optional[Amd]:
    """
    Read today's accumulation range and ask whether the open manipulated it.

    Accumulation : the high and low between amd_accum_start and amd_accum_end
                   (local to session_tz) on the current day.
    Manipulation : between amd_accum_end and amd_manip_end price trades beyond
                   one side of that range. With amd_reclaim it must also close
                   back inside - a sweep that keeps going is a breakout, not a trap.
    Distribution : the bias is the OPPOSITE of the side that was swept.

    Only candles up to i are read, so this is safe to call bar by bar.
    """
    tz = session_timezone(cfg)
    now = pd.Timestamp(bars.time[i])
    now = now.tz_localize("UTC") if now.tzinfo is None else now
    local_now = now.tz_convert(tz)
    today = local_now.date()

    lo_px, hi_px = None, None
    swept_high = swept_low = False
    reclaimed = False
    # walk back over today's candles only (a day is 288 M5 candles at most)
    start = max(0, i - 400)
    for j in range(start, i + 1):
        t = pd.Timestamp(bars.time[j])
        t = t.tz_localize("UTC") if t.tzinfo is None else t
        lt = t.tz_convert(tz)
        if lt.date() != today:
            continue
        clock = lt.time()
        if cfg.amd_accum_start <= clock < cfg.amd_accum_end:
            lo_px = bars.low[j] if lo_px is None else min(lo_px, bars.low[j])
            hi_px = bars.high[j] if hi_px is None else max(hi_px, bars.high[j])
        elif cfg.amd_accum_end <= clock < cfg.amd_manip_end and lo_px is not None:
            if bars.high[j] > hi_px:
                swept_high = True
            if bars.low[j] < lo_px:
                swept_low = True
            if (swept_high or swept_low) and lo_px <= bars.close[j] <= hi_px:
                reclaimed = True

    if lo_px is None or hi_px is None:
        return None
    if cfg.to_pips(hi_px - lo_px) < cfg.amd_min_range_pips:
        return None

    swept = ("both" if swept_high and swept_low else
             "high" if swept_high else "low" if swept_low else "")
    bias = 0
    if swept == "high":
        bias = -1                      # stops above were taken -> expect down
    elif swept == "low":
        bias = +1
    if cfg.amd_reclaim and bias != 0 and not reclaimed:
        bias = 0                       # it went through and stayed through
    return Amd(bias=bias, range_low=float(lo_px), range_high=float(hi_px),
               swept=swept, reclaimed=reclaimed)


def ob_quality(cfg: Config, ob_height: float, lq_touches: int, impulse_bars: int,
               pullback_bars: int, when: pd.Timestamp) -> Tuple[int, dict]:
    """
    How good is this order block? Returns (score, the parts that scored).

    Every part is something that can be measured on the chart, and every weight
    lives in Config, so a part can be switched off by setting its weight to 0.
    """
    parts: dict = {}
    score = 0
    if cfg.to_pips(ob_height) <= cfg.ob_small_pips:
        parts["small_ob"] = cfg.quality_small_ob
    if lq_touches >= cfg.lq_strong_touches:
        parts["lq_strong"] = cfg.quality_lq_strong
    if impulse_bars <= cfg.sharp_impulse_bars:
        parts["sharp_impulse"] = cfg.quality_sharp_impulse
    if impulse_bars > 0 and pullback_bars >= cfg.slow_return_ratio * impulse_bars:
        parts["slow_return"] = cfg.quality_slow_return
    try:
        ny_hour = when.tz_convert(session_timezone(cfg)).hour
        if cfg.first_hour_start <= ny_hour < cfg.first_hour_end:
            parts["first_hour"] = cfg.quality_first_hour
        elif (cfg.second_open_start >= 0
              and cfg.second_open_start <= ny_hour < cfg.second_open_end):
            parts["first_hour"] = cfg.quality_first_hour
    except Exception:
        pass
    score = sum(parts.values())
    return score, parts


def max_quality(cfg: Config) -> int:
    total = (cfg.quality_small_ob + cfg.quality_lq_strong + cfg.quality_sharp_impulse
             + cfg.quality_slow_return + cfg.quality_first_hour)
    return total + (cfg.amd_quality if cfg.amd_mode == "quality" else 0)


@dataclass
class PendingSetup:
    """
    One BOS + its order block, watched from the break until it resolves.

    Everything is stored as a TIMESTAMP, never as a candle index: the live bot
    and the backtest both hand the tracker a sliding window of candles, so an
    index that was correct when the setup was registered means something else a
    few candles later. Timestamps survive the window moving.
    """
    direction: int
    break_time: pd.Timestamp
    ob_time: pd.Timestamp
    ob_top: float
    ob_bottom: float
    ob_wick_top: float
    ob_wick_bottom: float
    bos_level: float
    impulse_bars: int
    state: str = "waiting"        # waiting | traded | failed
    reason: str = ""
    lq_seen: Optional["Liquidity"] = None
    inside_zone: bool = False     # price is in the zone now, so it is not a new touch
    touch_count: int = 0

    @property
    def alive(self) -> bool:
        return self.state == "waiting"

    @property
    def entry_edge(self) -> float:
        return self.ob_top if self.direction > 0 else self.ob_bottom

    @property
    def far_edge(self) -> float:
        return self.ob_bottom if self.direction > 0 else self.ob_top

    def stop_edge(self, cfg: "Config") -> float:
        if not (cfg.ob_use_body and cfg.sl_use_wick):
            return self.far_edge
        if self.direction > 0:
            return min(self.far_edge, self.ob_wick_bottom or self.far_edge)
        return max(self.far_edge, self.ob_wick_top or self.far_edge)

    def needed_pullback(self, cfg: Config) -> int:
        return max(cfg.min_pullback_bars, self.impulse_bars + cfg.pullback_extra_bars)


class SetupTracker:
    """
    The rules with a memory: each setup is watched from its break, re-checked on
    every closed candle, and decided AT THE TOUCH.

        tracker = SetupTracker(cfg)
        for each new closed candle:
            signal = tracker.update(bars)      # bars = a trailing window

    What kills a setup while it waits (check_setup):
        * it ran out of time            - ob_valid_bars since the break
        * a candle CLOSED beyond the far side of the block

    What happens when price finally touches the block:
        * pullback too fast (rule 3)    - fail if cfg.fail_on_touch, else the
                                          touch is wasted and the setup waits for
                                          a later, slower return
        * no liquidity taken (rule 4)   - same choice
        * otherwise                     - a Signal, and that block is finished

    `failures` keeps (time, side, reason) for everything that died, so a quiet
    session can still be explained.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.pending: List[PendingSetup] = []
        self.failures: List[Tuple[pd.Timestamp, str, str]] = []
        self.counters: dict = {}
        self._seen_breaks: set = set()
        self._done_obs: set = set()

    # ------------------------------------------------------------- internals
    def _tick(self, key: str) -> None:
        self.counters[key] = self.counters.get(key, 0) + 1

    def _fail(self, setup: PendingSetup, when: pd.Timestamp, reason: str) -> None:
        setup.state, setup.reason = "failed", reason
        self.failures.append((when, "BUY" if setup.direction > 0 else "SELL", reason))
        self._tick(f"setup failed: {reason.split(':')[0]}")

    @staticmethod
    def _index_of(bars: Bars, when: pd.Timestamp) -> int:
        """Where that timestamp sits in the current window (-1 if it fell off)."""
        pos = int(bars.time.searchsorted(when))
        if pos >= len(bars) or bars.time[pos] != when:
            return -1
        return pos

    def prime(self, bars: Bars) -> None:
        """Mark every break already visible in `bars` as seen, without watching it.

        For a tracker that starts mid-history (the live bot after a restart): the
        setups from those breaks may already have traded or failed before the
        restart, and an empty tracker cannot know. Priming makes it ignore them;
        everything from the next candle on is then tracked exactly as a tracker
        that had been running all along would track it.
        """
        highs, lows = find_swings(bars, self.cfg.swing_lookback)
        for bos in find_bos_events(bars, highs, lows, self.cfg):
            self._seen_breaks.add((pd.Timestamp(bars.time[bos.break_index]), bos.direction))

    # ------------------------------------------------------------------ main
    def update(self, bars: Bars, require_session: bool = True,
               trend: int = 0) -> Optional[Signal]:
        """`trend` = the higher-timeframe bias for the newest candle (see
        trend_series / trend_from_closes); only read when cfg.trend_filter is on."""
        cfg = self.cfg
        n = len(bars)
        if n < 2 * cfg.swing_lookback + 10:
            return None
        i = n - 1
        now = pd.Timestamp(bars.time[i])
        highs, lows = find_swings(bars, cfg.swing_lookback)

        # --- rules 1 and 2: register any break we have not seen before
        for bos in find_bos_events(bars, highs, lows, cfg):
            break_time = pd.Timestamp(bars.time[bos.break_index])
            stamp = (break_time, bos.direction)
            if stamp in self._seen_breaks:
                continue
            self._seen_breaks.add(stamp)
            if i - bos.break_index > cfg.ob_valid_bars:
                continue                      # already stale when we first saw it
            ob = find_order_block(bars, bos, cfg)
            if ob is None:
                self._tick("break with no usable order block")
                continue
            ob_time = pd.Timestamp(bars.time[ob.index])
            if ob_time in self._done_obs:
                continue
            if cfg.cancel_on_opposite_break:
                for other in self.pending:
                    if other.alive and other.direction != bos.direction:
                        self._fail(other, now, "structure broke the other way")
            self.pending.append(PendingSetup(
                direction=ob.direction, break_time=break_time, ob_time=ob_time,
                ob_top=float(ob.top), ob_bottom=float(ob.bottom),
                ob_wick_top=float(ob.wick_top), ob_wick_bottom=float(ob.wick_bottom),
                bos_level=float(bos.level), impulse_bars=int(ob.impulse_bars)))
            self._tick("setups being watched")

        # --- rules 3, 4, 5: watch each live setup
        signal = None
        for setup in self.pending:
            if not setup.alive:
                continue
            bi = self._index_of(bars, setup.break_time)
            if bi < 0:
                self._fail(setup, now, "expired: dropped out of the window")
                continue

            d = setup.direction
            age = i - bi
            if age > cfg.ob_valid_bars:
                self._fail(setup, now, f"expired: no valid touch within {cfg.ob_valid_bars} candles")
                continue
            # the block is gone if a candle CLOSED beyond its far side
            after_break = bars.close[bi + 1:i + 1]
            if len(after_break):
                if d > 0 and float(after_break.min()) < setup.ob_bottom:
                    self._fail(setup, now, "price closed below the order block")
                    continue
                if d < 0 and float(after_break.max()) > setup.ob_top:
                    self._fail(setup, now, "price closed above the order block")
                    continue

            edge = setup.entry_edge
            touched = bars.low[i] <= edge if d > 0 else bars.high[i] >= edge
            if not touched:
                setup.inside_zone = False
                continue
            if setup.inside_zone:
                continue                          # same visit, not a new touch
            setup.inside_zone = True
            setup.touch_count += 1

            # ---------- the touch decides ----------
            needed = setup.needed_pullback(cfg)
            if age < needed:
                if cfg.fail_on_touch:
                    self._fail(setup, now, f"came back too fast: {age} candles, needed {needed}")
                else:
                    self._tick("touch ignored: came back too fast")
                continue

            oi = self._index_of(bars, setup.ob_time)
            if oi < 0:
                self._fail(setup, now, "expired: the order block fell out of the window")
                continue
            ob = OrderBlock(direction=d, index=oi, top=setup.ob_top,
                            bottom=setup.ob_bottom, impulse_bars=setup.impulse_bars,
                            wick_top=setup.ob_wick_top, wick_bottom=setup.ob_wick_bottom)
            bos = Bos(direction=d, break_index=bi, level=setup.bos_level, swing_index=bi)

            if setup.lq_seen is None:
                setup.lq_seen = find_liquidity(bars, highs, lows, ob, bos, i, cfg)
            if setup.lq_seen is None:
                if cfg.fail_on_touch:
                    self._fail(setup, now, "reached the order block without taking liquidity")
                else:
                    self._tick("touch ignored: no liquidity taken yet")
                continue
            if require_session and not in_session(now, cfg):
                self._tick("touch ignored: outside the session")
                continue
            if cfg.trend_filter != "off" and trend != d:
                self._tick("touch ignored: against the higher-timeframe trend")
                continue
            if cfg.entry_confirm_close and not (
                    bars.close[i] > edge if d > 0 else bars.close[i] < edge):
                self._tick("touch ignored: candle did not close back outside the OB")
                continue

            quality, parts = ob_quality(cfg, setup.ob_top - setup.ob_bottom,
                                        setup.lq_seen.touches, setup.impulse_bars, age, now)
            if cfg.amd_mode != "off":
                amd = day_amd(bars, i, cfg)
                bias = amd.bias if amd else 0
                if cfg.amd_mode == "filter" and bias != d:
                    self._tick("touch ignored: against the AMD bias"
                               if bias else "touch ignored: no AMD manipulation today")
                    continue
                if cfg.amd_mode == "quality" and bias == d:
                    parts["amd"] = cfg.amd_quality
                    quality += cfg.amd_quality
            if quality < cfg.min_quality:
                if cfg.fail_on_touch:
                    self._fail(setup, now, f"order block not good enough: "
                                           f"quality {quality} < {cfg.min_quality}")
                else:
                    self._tick(f"touch ignored: quality {quality} < {cfg.min_quality}")
                continue

            stop_ref = setup.stop_edge(cfg)
            sl = (stop_ref - cfg.pips(cfg.sl_buffer_pips) if d > 0
                  else stop_ref + cfg.pips(cfg.sl_buffer_pips))
            candidate = Signal(time=now, direction=d, touch_price=float(edge), sl=float(sl),
                               ob=ob, bos=bos, lq=setup.lq_seen,
                               impulse_bars=setup.impulse_bars, pullback_bars=age,
                               ob_time=setup.ob_time, quality=quality, quality_parts=parts)
            setup.state, setup.reason = "traded", "touched while valid"
            self._done_obs.add(setup.ob_time)
            self._tick("SIGNALS")
            if signal is None:
                signal = candidate

        if len(self.pending) > 60:
            self.pending = [x for x in self.pending if x.alive][-60:]
        return signal

    # -------------------------------------------------------------- reporting
    def waiting(self) -> List[PendingSetup]:
        return [x for x in self.pending if x.alive]

    def state_line(self, bars: Bars) -> str:
        live = self.waiting()
        if not live:
            return "no setup is being watched"
        parts = []
        for x in live[-3:]:
            side = "BUY" if x.direction > 0 else "SELL"
            parts.append(f"{side} OB {x.ob_bottom:.2f}-{x.ob_top:.2f}"
                         f"{' LQ ok' if x.lq_seen else ' no LQ yet'}")
        return f"{len(live)} setup(s) waiting: " + "; ".join(parts)


class DayLimit:
    """max_trades_per_day, reset on each new session date."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._date = None
        self.count = 0

    def _day(self, ts):
        ts = pd.Timestamp(ts)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        return ts.tz_convert(session_timezone(self.cfg)).date()

    def allows(self, ts, quality: int = 0) -> bool:
        """
        Your rule: at least a few trades a day, and a GOOD setup may go beyond the
        normal cap - extra_trades_if_quality says how far.
        """
        d = self._day(ts)
        if d != self._date:
            self._date, self.count = d, 0
        cap = self.cfg.max_trades_per_day
        if quality >= self.cfg.good_quality:
            cap += self.cfg.extra_trades_if_quality
        return self.count < cap

    def register(self, ts) -> None:
        d = self._day(ts)
        if d != self._date:
            self._date, self.count = d, 0
        self.count += 1
