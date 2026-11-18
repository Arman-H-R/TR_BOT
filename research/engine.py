# -*- coding: utf-8 -*-
"""
research/engine.py - a fast, two-stage copy of backtest.run() for optimisation.

Stage 1  candidates(df, cfg)      the SetupTracker, but it never "spends" a setup:
                                   every valid touch (pullback ok + liquidity taken)
                                   is recorded with its features. Slow (~40 s on
                                   100k M5 candles), depends only on STRUCTURE
                                   parameters (swings, OB, LQ, pullback ...).
Stage 2  run(cands, market, p)    picks the signals the real tracker would have
                                   fired under the FILTER parameters (session,
                                   quality, AMD, trend ...), applies the bot's
                                   gating (one position, daily cap, one trade per
                                   OB) and simulates the exits (SL/TP/BE/trail/
                                   time stop) with costs. Fast (~50 ms).

Stage 2 reproduces backtest.run() for the shipped strategy (checked by
research/check_engine.py); every final number in the report is re-run through
backtest.run() itself, this file is only for searching.

Results are in R (pnl / money at risk); equity is compounded as
equity *= 1 + risk% * R, which is what a fixed-fractional bot does minus the
0.01-lot rounding.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, asdict, replace
from typing import Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import smc_core as core  # noqa: E402

NY = "America/New_York"


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def load(tf: str = "M5", start=None, end=None, spread: float = 0.30) -> pd.DataFrame:
    path = os.path.join(ROOT, "data", f"XAUUSD_{tf}.csv")
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    # the MT5 bar "spread" is the MINIMUM spread inside the candle, and it is
    # 0.09-0.16 on old candles but 0.30 on recent ones - so a fixed 0.30 (today's
    # live spread, what backtest.py charges) is used unless spread= is given
    df["spread_px"] = float(spread)
    if start is not None:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df.index < pd.Timestamp(end, tz="UTC")]
    return df[["open", "high", "low", "close", "spread_px"]]


@dataclass
class Market:
    """Per-candle arrays every filter / exit needs, computed once."""
    time: pd.DatetimeIndex
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    spread: np.ndarray
    ny_min: np.ndarray        # minutes since NY midnight
    ny_day: np.ndarray        # NY calendar day as int (days since epoch)
    ny_wd: np.ndarray         # NY weekday 0=Mon
    atr: np.ndarray           # ATR(14) of this timeframe, price units
    h1_trend: np.ndarray      # +1/-1: last CLOSED H1 close vs EMA50(H1)
    h4_trend: np.ndarray      # +1/-1: last CLOSED H4 close vs EMA50(H4)
    d1_trend: np.ndarray      # +1/-1: last CLOSED day close vs EMA20(D1)
    amd: dict = field(default_factory=dict)   # cache: params -> bias array

    def __len__(self):
        return len(self.c)


def _htf_trend(df: pd.DataFrame, rule: str, span: int) -> np.ndarray:
    """Trend of a higher timeframe using only bars CLOSED before each candle."""
    htf = df["close"].resample(rule, label="left", closed="left").last().dropna()
    ema = htf.ewm(span=span, adjust=False).mean()
    sign = np.sign(htf - ema)
    # an H1 bar labelled 10:00 closes at 11:00 -> usable from 11:00 on
    step = pd.Timedelta(rule)
    usable = pd.Series(sign.values, index=sign.index + step)
    aligned = usable.reindex(df.index, method="ffill")
    return aligned.fillna(0).to_numpy()


def market(df: pd.DataFrame) -> Market:
    idx = df.index
    local = idx.tz_convert(NY)
    ny_min = (local.hour * 60 + local.minute).to_numpy()
    ny_day = (local.normalize().tz_localize(None).asi8 // 86_400_000_000_000).astype(np.int64)
    h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - prev), abs(l - prev)))
    atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    return Market(time=idx, o=df["open"].to_numpy(float), h=h, l=l, c=c,
                  spread=df["spread_px"].to_numpy(float), ny_min=ny_min, ny_day=ny_day,
                  ny_wd=local.weekday.to_numpy(), atr=atr,
                  h1_trend=_htf_trend(df, "1h", 50), h4_trend=_htf_trend(df, "4h", 50),
                  d1_trend=_htf_trend(df, "1D", 20))


# --------------------------------------------------------------------------- #
# stage 1: candidates
# --------------------------------------------------------------------------- #
class CandidateTracker(core.SetupTracker):
    """SetupTracker.update() with the filters and the 'traded' state removed:
    every touch that passes rules 3 + 4 is written to `sink`."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.sid = 0
        self.sink: list = []

    def scan(self, bars: core.Bars, gi: int) -> None:
        cfg = self.cfg
        n = len(bars)
        i = n - 1
        now = pd.Timestamp(bars.time[i])
        highs, lows = core.find_swings(bars, cfg.swing_lookback)
        for bos in core.find_bos_events(bars, highs, lows, cfg):
            break_time = pd.Timestamp(bars.time[bos.break_index])
            stamp = (break_time, bos.direction)
            if stamp in self._seen_breaks:
                continue
            self._seen_breaks.add(stamp)
            if i - bos.break_index > cfg.ob_valid_bars:
                continue
            ob = core.find_order_block(bars, bos, cfg)
            if ob is None:
                continue
            if cfg.cancel_on_opposite_break:
                for other in self.pending:
                    if other.alive and other.direction != bos.direction:
                        other.state = "failed"
            s = core.PendingSetup(
                direction=ob.direction, break_time=break_time,
                ob_time=pd.Timestamp(bars.time[ob.index]),
                ob_top=float(ob.top), ob_bottom=float(ob.bottom),
                ob_wick_top=float(ob.wick_top), ob_wick_bottom=float(ob.wick_bottom),
                bos_level=float(bos.level), impulse_bars=int(ob.impulse_bars))
            s.sid, s.reg_i = self.sid, gi
            self.sid += 1
            self.pending.append(s)

        for setup in self.pending:
            if not setup.alive:
                continue
            bi = self._index_of(bars, setup.break_time)
            if bi < 0:
                setup.state = "failed"
                continue
            d = setup.direction
            age = i - bi
            if age > cfg.ob_valid_bars:
                setup.state = "failed"
                continue
            after = bars.close[bi + 1:i + 1]
            if len(after):
                if d > 0 and float(after.min()) < setup.ob_bottom:
                    setup.state = "failed"
                    continue
                if d < 0 and float(after.max()) > setup.ob_top:
                    setup.state = "failed"
                    continue
            edge = setup.entry_edge
            touched = bars.low[i] <= edge if d > 0 else bars.high[i] >= edge
            if not touched:
                setup.inside_zone = False
                continue
            if setup.inside_zone:
                continue
            setup.inside_zone = True
            setup.touch_count += 1
            if age < setup.needed_pullback(cfg):
                continue
            oi = self._index_of(bars, setup.ob_time)
            if oi < 0:
                setup.state = "failed"
                continue
            if setup.lq_seen is None:
                ob = core.OrderBlock(d, oi, setup.ob_top, setup.ob_bottom, setup.impulse_bars,
                                     setup.ob_wick_top, setup.ob_wick_bottom)
                bos = core.Bos(d, bi, setup.bos_level, bi)
                setup.lq_seen = core.find_liquidity(bars, highs, lows, ob, bos, i, cfg)
            if setup.lq_seen is None:
                continue
            self.sink.append((gi, setup.sid, setup.reg_i, d, setup.ob_time.value,
                              setup.ob_top, setup.ob_bottom, setup.stop_edge(cfg),
                              setup.bos_level, setup.impulse_bars, age,
                              setup.lq_seen.touches, setup.lq_seen.level, setup.touch_count))
        if len(self.pending) > 60:
            self.pending = [x for x in self.pending if x.alive][-60:]


CAND_COLS = ["i", "sid", "reg_i", "d", "ob_time", "ob_top", "ob_bottom", "stop_edge",
             "bos_level", "impulse", "age", "lq_touches", "lq_level", "touch_n"]


def candidates(df: pd.DataFrame, cfg: core.Config) -> pd.DataFrame:
    bars = core.Bars.from_dataframe(df[["open", "high", "low", "close"]])
    need = max(cfg.required_bars(), 200)
    tr = CandidateTracker(cfg)
    for i in range(need, len(bars)):
        tr.scan(bars.window(i - need + 1, i + 1), i)
    out = pd.DataFrame(tr.sink, columns=CAND_COLS)
    out.attrs["first_bar"] = need
    return out


# --------------------------------------------------------------------------- #
# stage 2 parameters
# --------------------------------------------------------------------------- #
@dataclass
class Params:
    # session (NY local, minutes) ------------------------------------------
    sess_start: int = 8 * 60
    sess_end: int = 17 * 60
    weekdays: tuple = (0, 1, 2, 3, 4)
    # quality ----------------------------------------------------------------
    q_small: int = 2
    q_lq: int = 2
    q_sharp: int = 1
    q_slow: int = 1
    q_first: int = 2
    ob_small_pips: float = 20.0
    lq_strong: int = 3
    sharp_bars: int = 2
    slow_ratio: float = 8.0
    first_start: int = 8 * 60
    first_end: int = 9 * 60
    min_quality: int = 1
    # extra filters (all off = the shipped bot) -----------------------------
    trend_tf: str = ""            # "" | "h1" | "h4" | "d1"
    trend_mode: str = "off"       # off | with | against
    side: int = 0                 # 0 both, +1 buys only, -1 sells only
    confirm: str = "none"         # none | close_out (touch candle closes back outside the zone)
                                  #      | color (touch candle closes in trade direction)
    max_touch: int = 0            # 0 = any touch number
    min_atr_mult: float = 0.0     # stop distance >= x * ATR
    max_atr_mult: float = 0.0     # stop distance <= x * ATR (0 = off)
    amd_mode: str = "off"
    # risk / exits -----------------------------------------------------------
    sl_buffer_pips: float = 4.0
    min_sl_pips: float = 4.0
    max_sl_pips: float = 150.0
    rr: float = 4.0
    be_r: float = 1.0             # 0 = no break-even
    be_cost_pips: float = 2.0
    trail_start: float = 0.0      # 0 = off; else step trail from this R
    trail_gap: float = 1.0
    atr_trail: float = 0.0        # 0 = off; chandelier trail at x * ATR once BE done
    time_stop: int = 0            # bars; 0 = off. close at market after N bars
    close_session_end: bool = False
    friday_close: int = 0         # NY minutes; 0 = off. On Fridays no entry at/after
                                  # this time and any position is closed at it
    entry: str = "market"         # market (close of touch candle) | limit (at OB edge)
    gap_fill: bool = True         # a candle opening beyond the stop fills at its open
    # gating -----------------------------------------------------------------
    max_per_day: int = 3
    extra_q: int = 2
    good_q: int = 5
    # costs ------------------------------------------------------------------
    slip: float = 0.0             # price units lost on market entry and on stop exits
    commission: float = 0.0       # USD per lot round turn
    contract: float = 100.0
    pip: float = 0.10
    stops_level: float = 0.05     # broker min stop distance, price units

    def key(self):
        return tuple(sorted(asdict(self).items()))


COSTS = {
    "base": dict(slip=0.0, commission=0.0),      # what backtest.py charges: spread only
    "real": dict(slip=0.05, commission=5.0),     # + 5c slippage, 5 USD/lot commission
    "stress": dict(slip=0.15, commission=7.0),   # bad fills
}


# --------------------------------------------------------------------------- #
# AMD bias per candle (vectorised version of core.day_amd)
# --------------------------------------------------------------------------- #
def amd_bias(m: Market, accum=(120, 480), manip_end=600, min_range=2.0, reclaim=True):
    key = (accum, manip_end, min_range, reclaim)
    if key in m.amd:
        return m.amd[key]
    n = len(m)
    bias = np.zeros(n, np.int8)
    days = m.ny_day
    starts = np.flatnonzero(np.r_[True, days[1:] != days[:-1]])
    ends = np.r_[starts[1:], n]
    for s, e in zip(starts, ends):
        mm = m.ny_min[s:e]
        acc = (mm >= accum[0]) & (mm < accum[1])
        if not acc.any():
            continue
        # running accumulation range only becomes final at accum end; bias only
        # exists from accum end on, so use the full range
        lo, hi = m.l[s:e][acc].min(), m.h[s:e][acc].max()
        if hi - lo < min_range:
            continue
        man = (mm >= accum[1]) & (mm < manip_end)
        sh = np.maximum.accumulate(man & (m.h[s:e] > hi))
        sl = np.maximum.accumulate(man & (m.l[s:e] < lo))
        inside = (m.c[s:e] >= lo) & (m.c[s:e] <= hi)
        rec = np.maximum.accumulate(man & (sh | sl) & inside)
        b = np.where(sh & ~sl, -1, np.where(sl & ~sh, 1, 0))
        if reclaim:
            b = np.where(rec, b, 0)
        b[mm < accum[1]] = 0
        bias[s:e] = b
    m.amd[key] = bias
    return bias


# --------------------------------------------------------------------------- #
# stage 2: select, gate, simulate
# --------------------------------------------------------------------------- #
def quality(c: pd.DataFrame, m: Market, p: Params) -> np.ndarray:
    hgt = (c.ob_top - c.ob_bottom).to_numpy() / p.pip
    mins = m.ny_min[c.i.to_numpy()]
    q = np.where(hgt <= p.ob_small_pips, p.q_small, 0)
    q = q + np.where(c.lq_touches.to_numpy() >= p.lq_strong, p.q_lq, 0)
    imp = c.impulse.to_numpy()
    q = q + np.where(imp <= p.sharp_bars, p.q_sharp, 0)
    q = q + np.where((imp > 0) & (c.age.to_numpy() >= p.slow_ratio * imp), p.q_slow, 0)
    q = q + np.where((mins >= p.first_start) & (mins < p.first_end), p.q_first, 0)
    return q


def eligible(c: pd.DataFrame, m: Market, p: Params) -> np.ndarray:
    """Which candidate touches the real tracker would turn into a signal."""
    i = c.i.to_numpy()
    d = c.d.to_numpy()
    mins = m.ny_min[i]
    ok = (mins >= p.sess_start) & (mins < p.sess_end) & np.isin(m.ny_wd[i], p.weekdays)
    ok &= quality(c, m, p) >= p.min_quality
    if p.amd_mode == "filter":
        ok &= amd_bias(m)[i] == d
    if p.trend_mode != "off" and p.trend_tf:
        t = getattr(m, f"{p.trend_tf}_trend")[i]
        ok &= (t == d) if p.trend_mode == "with" else (t == -d)
    if p.side:
        ok &= d == p.side
    if p.confirm == "close_out":
        edge = np.where(d > 0, c.ob_top, c.ob_bottom)
        ok &= np.where(d > 0, m.c[i] > edge, m.c[i] < edge)
    elif p.confirm == "color":
        ok &= np.where(d > 0, m.c[i] > m.o[i], m.c[i] < m.o[i])
    if p.max_touch:
        ok &= c.touch_n.to_numpy() <= p.max_touch
    return ok


def select(c: pd.DataFrame, ok: np.ndarray) -> pd.DataFrame:
    """The tracker's bookkeeping: first eligible touch per setup, one signal per
    candle (the rest of that candle's touches are spent), an OB already signalled
    before a later break registers it is skipped."""
    if not len(c):
        return c
    c = c.assign(ok=ok).sort_values(["i", "sid"], kind="stable")
    done_setup = set()
    ob_done = {}
    last_bar = -1
    keep = []
    for row in c.itertuples(index=False):
        if not row.ok or row.sid in done_setup:
            continue
        t = ob_done.get(row.ob_time)
        if t is not None and t < row.reg_i:
            continue
        done_setup.add(row.sid)
        if t is None:
            ob_done[row.ob_time] = row.i
        if row.i == last_bar:
            continue                       # a second signal on the same candle is lost
        last_bar = row.i
        keep.append(row)
    return pd.DataFrame(keep, columns=c.columns)


def simulate(sig: pd.DataFrame, m: Market, p: Params, quality_arr=None):
    """Gating + exits. Returns a DataFrame of trades (R already net of costs)."""
    out = []
    busy_until = -1
    day_count = {}
    used = set()
    n = len(m)
    q_all = quality_arr if quality_arr is not None else np.zeros(len(sig), int)
    for k, row in enumerate(sig.itertuples(index=False)):
        i = row.i
        if i < busy_until:
            continue
        if row.ob_time in used:
            continue
        if p.friday_close and m.ny_wd[i] == 4 and m.ny_min[i] >= p.friday_close:
            continue
        day = m.ny_day[i]
        cap = p.max_per_day + (p.extra_q if q_all[k] >= p.good_q else 0)
        if day_count.get(day, 0) >= cap:
            continue
        d = row.d
        spr = m.spread[i]
        sl = row.stop_edge - d * p.sl_buffer_pips * p.pip
        if p.entry == "limit":
            # upper bound: a resting limit at the edge, as if validity were known
            # before the touch (it is not always - the LQ sweep can be the touch)
            edge = row.ob_top if d > 0 else row.ob_bottom
            if d > 0 and m.l[i] + spr > edge:
                continue
            fill = edge
        else:
            fill = (m.c[i] + spr if d > 0 else m.c[i]) + d * p.slip
        risk = (fill - sl) * d
        if risk <= 0:
            continue
        sl_pips = risk / p.pip
        if not (p.min_sl_pips <= sl_pips <= p.max_sl_pips):
            continue
        tp = fill + d * p.rr * risk
        if p.stops_level and (abs(fill - sl) < p.stops_level or abs(tp - fill) < p.stops_level):
            continue
        if p.min_atr_mult and risk < p.min_atr_mult * m.atr[i]:
            continue
        if p.max_atr_mult and risk > p.max_atr_mult * m.atr[i]:
            continue
        used.add(row.ob_time)
        day_count[day] = day_count.get(day, 0) + 1

        # ---- exits
        cur_sl = sl
        be_done = False
        lock = 0.0
        be_trig = fill + d * p.be_r * risk if p.be_r > 0 else None
        be_px = fill + d * p.be_cost_pips * p.pip
        best = fill
        exit_px, why, j = None, "END", n - 1
        for j in range(i + 1, n):
            s = m.spread[j] if d < 0 else 0.0
            hi, lo = m.h[j] + s, m.l[j] + s
            hit_sl = lo <= cur_sl if d > 0 else hi >= cur_sl
            hit_tp = hi >= tp if d > 0 else lo <= tp
            if hit_sl:
                why = "TRAIL" if lock > 0 else "BE" if be_done else "SL"
                # a gap through the stop fills at the open
                op = m.o[j] + s
                gap = p.gap_fill and ((op < cur_sl) if d > 0 else (op > cur_sl))
                exit_px = (op if gap else cur_sl) - d * p.slip
                break
            if hit_tp:
                exit_px, why = tp, "TP"
                break
            # manage on this candle's range (after the exit checks, like the bot)
            if be_trig is not None and not be_done:
                if (hi >= be_trig) if d > 0 else (lo <= be_trig):
                    if (be_px - cur_sl) * d > 0:
                        cur_sl = be_px
                        be_done = True
            if p.trail_start > 0:
                bp = hi if d > 0 else lo
                reached = (bp - fill) * d / risk
                if reached >= p.trail_start:
                    kk = p.trail_start + int(reached - p.trail_start)
                    lk = kk - p.trail_gap
                    if lk > lock:
                        new = fill + d * lk * risk
                        if (new - cur_sl) * d > 0:
                            cur_sl, lock, be_done = new, lk, True
            if p.atr_trail > 0 and be_done:
                best = max(best, hi) if d > 0 else min(best, lo)
                new = best - d * p.atr_trail * m.atr[j]
                if (new - cur_sl) * d > 0:
                    cur_sl, lock = new, max(lock, 1e-9)
            if p.time_stop and j - i >= p.time_stop:
                exit_px, why = m.c[j] + (m.spread[j] if d < 0 else 0.0), "TIME"
                break
            if p.friday_close and ((m.ny_wd[j] == 4 and m.ny_min[j] >= p.friday_close)
                                   or m.ny_wd[j] >= 5
                                   or (m.ny_day[j] - m.ny_day[i] >= 3 and m.ny_wd[j] < m.ny_wd[i])):
                exit_px, why = m.c[j] + (m.spread[j] if d < 0 else 0.0), "FRIDAY"
                break
            if p.close_session_end and not (p.sess_start <= m.ny_min[j] < p.sess_end):
                exit_px, why = m.c[j] + (m.spread[j] if d < 0 else 0.0), "SESSION"
                break
        if exit_px is None:
            exit_px = m.c[n - 1] + (m.spread[n - 1] if d < 0 else 0.0)
        cost_r = p.commission / (p.contract * risk)
        r = (exit_px - fill) * d / risk - cost_r
        out.append((i, j, d, fill, sl, tp, exit_px, risk / p.pip, r, why, be_done, q_all[k]))
        busy_until = j          # the bot is flat again from the exit candle on
    t = pd.DataFrame(out, columns=["i", "j", "d", "entry", "sl", "tp", "exit", "risk_pips",
                                   "r", "outcome", "be_done", "quality"])
    t["entry_time"] = m.time[t.i.to_numpy()] if len(t) else pd.Series(dtype="datetime64[ns, UTC]")
    t["exit_time"] = m.time[t.j.to_numpy()] if len(t) else pd.Series(dtype="datetime64[ns, UTC]")
    return t


def run(c: pd.DataFrame, m: Market, p: Params, start=None, end=None) -> pd.DataFrame:
    ok = eligible(c, m, p)
    sig = select(c, ok)
    q = quality(sig, m, p) if len(sig) else np.zeros(0, int)
    t = simulate(sig, m, p, q)
    if start is not None:
        t = t[t.entry_time >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        t = t[t.entry_time < pd.Timestamp(end, tz="UTC")]
    return t


# --------------------------------------------------------------------------- #
# stage 1, fast: same answer as candidates(), one pass instead of one window
# re-scan per candle (swings and breaks are computed once over the whole series;
# the window limits of the live bot are emulated explicitly)
# --------------------------------------------------------------------------- #
def _global_breaks(bars: core.Bars, highs, lows, k: int, need: int):
    """find_bos_events() as seen by the window ending at each break candle."""
    events = []
    hi = li = 0
    last_high = last_low = None
    c = bars.close
    for i in range(len(c)):
        while hi < len(highs) and highs[hi][0] + k <= i:
            last_high = highs[hi]
            hi += 1
        while li < len(lows) and lows[li][0] + k <= i:
            last_low = lows[li]
            li += 1
        first_visible = i - need + 1 + k      # pivots before this are not in the window
        lh = last_high if (last_high is not None and last_high[0] >= first_visible) else None
        ll = last_low if (last_low is not None and last_low[0] >= first_visible) else None
        if lh is not None and c[i] > lh[1]:
            events.append(core.Bos(+1, i, lh[1], lh[0]))
            last_high = None
        elif ll is not None and c[i] < ll[1]:
            events.append(core.Bos(-1, i, ll[1], ll[0]))
            last_low = None
    return events


def candidates_fast(df: pd.DataFrame, cfg: core.Config) -> pd.DataFrame:
    import bisect
    bars = core.Bars.from_dataframe(df[["open", "high", "low", "close"]])
    n = len(bars)
    k = cfg.swing_lookback
    need = max(cfg.required_bars(), 200)
    highs, lows = core.find_swings(bars, k)
    hi_idx = [p[0] for p in highs]
    lo_idx = [p[0] for p in lows]
    events = _global_breaks(bars, highs, lows, k, need)
    by_bar = {}
    for e in events:
        by_bar.setdefault(e.break_index, []).append(e)
    lo_arr, hi_arr, cl = bars.low, bars.high, bars.close
    pending = []
    sink = []
    sid = 0
    done_breaks = set()
    for i in range(need, n):
        wstart = i - need + 1
        for bos in by_bar.get(i, ()):
            if bos.swing_index < wstart:            # pragma: no cover - emulated above
                continue
            ob = core.find_order_block(bars, bos, cfg)
            if ob is None or ob.index < wstart:
                continue
            if cfg.cancel_on_opposite_break:
                for o in pending:
                    if o["alive"] and o["d"] != bos.direction:
                        o["alive"] = False
            edge = ob.top if ob.direction > 0 else ob.bottom
            far = ob.bottom if ob.direction > 0 else ob.top
            stop = far
            if cfg.ob_use_body and cfg.sl_use_wick:
                stop = (min(far, ob.wick_bottom or far) if ob.direction > 0
                        else max(far, ob.wick_top or far))
            pending.append(dict(sid=sid, reg=i, d=ob.direction, bi=i, oi=ob.index,
                                top=ob.top, bot=ob.bottom, edge=edge, stop=stop,
                                bos=bos, ob=ob, imp=ob.impulse_bars,
                                needed=max(cfg.min_pullback_bars,
                                           ob.impulse_bars + cfg.pullback_extra_bars),
                                alive=True, inside=False, touches=0, lq=None,
                                ob_time=bars.time[ob.index].value))
            sid += 1
        for s in pending:
            if not s["alive"]:
                continue
            d, bi = s["d"], s["bi"]
            age = i - bi
            if bi < wstart or age > cfg.ob_valid_bars:
                s["alive"] = False
                continue
            if age > 0 and ((d > 0 and cl[i] < s["bot"]) or (d < 0 and cl[i] > s["top"])):
                s["alive"] = False
                continue
            touched = lo_arr[i] <= s["edge"] if d > 0 else hi_arr[i] >= s["edge"]
            if not touched:
                s["inside"] = False
                continue
            if s["inside"]:
                continue
            s["inside"] = True
            s["touches"] += 1
            if age < s["needed"]:
                continue
            if s["oi"] < wstart:
                s["alive"] = False
                continue
            if s["lq"] is None:
                lo_b = max(wstart, s["oi"] - cfg.lq_lookback_bars)
                top_b = i - k                     # a pivot needs k candles on its right
                if d > 0:
                    a, b = bisect.bisect_left(lo_idx, lo_b), bisect.bisect_right(lo_idx, top_b)
                    piv_lo, piv_hi = lows[a:b], []
                else:
                    a, b = bisect.bisect_left(hi_idx, lo_b), bisect.bisect_right(hi_idx, top_b)
                    piv_lo, piv_hi = [], highs[a:b]
                s["lq"] = core.find_liquidity(bars, piv_hi, piv_lo, s["ob"], s["bos"], i, cfg)
            if s["lq"] is None:
                continue
            sink.append((i, s["sid"], s["reg"], d, s["ob_time"], s["top"], s["bot"], s["stop"],
                         s["bos"].level, s["imp"], age, s["lq"].touches, s["lq"].level,
                         s["touches"]))
        if len(pending) > 60:
            pending = [x for x in pending if x["alive"]][-60:]
    out = pd.DataFrame(sink, columns=CAND_COLS)
    out.attrs["first_bar"] = need
    return out
