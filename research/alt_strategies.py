# -*- coding: utf-8 -*-
"""
research/alt_strategies.py - non-SMC strategies, built to be compared with the
SMC bot under exactly the same execution and cost model (engine.simulate()).

Each generator returns signals in the engine's candidate format:
    i          candle that closes with the signal (market entry at its close)
    d          +1 buy / -1 sell
    stop_edge  the stop price BEFORE sl_buffer_pips (use sl_buffer_pips=0 if
               the generator already includes its buffer)
    ob_time    a unique key (one trade per key)

ORB      NY opening range breakout: range of the first `length` minutes after
         `start`, enter on the first close beyond it, stop at the other side
         (or the middle).
SWEEP    pre-NY range liquidity sweep: the range of [r0, r1) NY time; between
         w0 and w1 a candle trades beyond one side and CLOSES back inside ->
         trade back into the range, stop behind the sweep extreme.
PULLBACK higher-timeframe trend + M5 pullback: H1 close above EMA50(H1), the
         candle dips to EMA(fast) and closes back above it, green -> buy;
         stop under the lowest low of the last `swing` candles.
DONCHIAN close beyond the highest high / lowest low of `look` candles, in the
         H4 trend direction, stop at atr_k x ATR.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.engine import CAND_COLS, Market


def _frame(rows):
    out = pd.DataFrame(rows, columns=["i", "d", "stop_edge", "key"])
    if not len(out):
        return pd.DataFrame(columns=CAND_COLS)
    out = out.sort_values("i").drop_duplicates("i").reset_index(drop=True)
    return pd.DataFrame(dict(
        i=out.i.astype(int), sid=np.arange(len(out)), reg_i=out.i.astype(int), d=out.d.astype(int),
        ob_time=out.key.astype(np.int64), ob_top=out.stop_edge, ob_bottom=out.stop_edge,
        stop_edge=out.stop_edge, bos_level=0.0, impulse=0, age=0, lq_touches=0, lq_level=0.0,
        touch_n=1))


def _days(m: Market):
    d = m.ny_day
    starts = np.flatnonzero(np.r_[True, d[1:] != d[:-1]])
    return zip(starts, np.r_[starts[1:], len(d)])


def orb(m: Market, start=570, length=30, cutoff=720, stop="range", trend=""):
    rows = []
    tr = getattr(m, f"{trend}_trend") if trend else None
    for s, e in _days(m):
        mm = m.ny_min[s:e]
        rng = (mm >= start) & (mm < start + length)
        if rng.sum() < max(1, length // 5 - 1) or m.ny_wd[s] >= 5:
            continue
        hi, lo = m.h[s:e][rng].max(), m.l[s:e][rng].min()
        after = np.flatnonzero((mm >= start + length) & (mm < cutoff))
        for j in after:
            i = s + j
            c = m.c[i]
            d = 1 if c > hi else -1 if c < lo else 0
            if d == 0:
                continue
            if tr is not None and tr[i] != d:
                break
            sl = (lo if d > 0 else hi) if stop == "range" else (hi + lo) / 2
            rows.append((i, d, sl, int(m.ny_day[i])))
            break
    return _frame(rows)


def sweep(m: Market, r0=0, r1=480, w0=480, w1=720, trend="", against_trend=False):
    rows = []
    tr = getattr(m, f"{trend}_trend") if trend else None
    for s, e in _days(m):
        mm = m.ny_min[s:e]
        rng = (mm >= r0) & (mm < r1)
        if rng.sum() < 6 or m.ny_wd[s] >= 5:
            continue
        hi, lo = m.h[s:e][rng].max(), m.l[s:e][rng].min()
        win = np.flatnonzero((mm >= w0) & (mm < w1))
        ext_hi, ext_lo = -np.inf, np.inf
        for j in win:
            i = s + j
            ext_hi, ext_lo = max(ext_hi, m.h[i]), min(ext_lo, m.l[i])
            d = 0
            if m.h[i] > hi and m.c[i] < hi:
                d, sl = -1, ext_hi
            elif m.l[i] < lo and m.c[i] > lo:
                d, sl = 1, ext_lo
            if d == 0:
                continue
            if tr is not None and (tr[i] != d) != against_trend:
                continue
            rows.append((i, d, sl, int(m.ny_day[i])))
            break
    return _frame(rows)


def pullback(m: Market, fast=20, swing=6, w0=480, w1=1020, trend="h1"):
    ema = pd.Series(m.c).ewm(span=fast, adjust=False).mean().to_numpy()
    tr = getattr(m, f"{trend}_trend")
    rows = []
    n = len(m.c)
    for i in range(max(fast, swing) + 1, n):
        if not (w0 <= m.ny_min[i] < w1) or m.ny_wd[i] >= 5:
            continue
        d = tr[i]
        if d > 0 and m.l[i] <= ema[i] < m.c[i] and m.c[i] > m.o[i]:
            rows.append((i, 1, m.l[i - swing + 1:i + 1].min(), i))
        elif d < 0 and m.h[i] >= ema[i] > m.c[i] and m.c[i] < m.o[i]:
            rows.append((i, -1, m.h[i - swing + 1:i + 1].max(), i))
    return _frame(rows)


def donchian(m: Market, look=48, atr_k=2.0, w0=480, w1=1020, trend="h4"):
    hh = pd.Series(m.h).rolling(look).max().shift(1).to_numpy()
    ll = pd.Series(m.l).rolling(look).min().shift(1).to_numpy()
    tr = getattr(m, f"{trend}_trend") if trend else np.zeros(len(m.c))
    rows = []
    for i in range(look + 1, len(m.c)):
        if not (w0 <= m.ny_min[i] < w1) or m.ny_wd[i] >= 5:
            continue
        if m.c[i] > hh[i] and (not trend or tr[i] > 0):
            rows.append((i, 1, m.c[i] - atr_k * m.atr[i], i))
        elif m.c[i] < ll[i] and (not trend or tr[i] < 0):
            rows.append((i, -1, m.c[i] + atr_k * m.atr[i], i))
    return _frame(rows)
