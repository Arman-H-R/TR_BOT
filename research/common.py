# -*- coding: utf-8 -*-
"""Shared research settings: periods, cost levels, the scoring rule."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataclasses import replace
import numpy as np, pandas as pd
from research import engine as E, metrics as M

# M5 history available from the broker: 2025-06-23 .. 2026-11-18 (99 999 candles)
IS = ("2025-07-01", "2026-03-01")        # in-sample: every choice is made here
OOS = ("2026-03-01", "2026-11-19")       # out-of-sample: looked at only to verify
FULL = ("2025-07-01", "2026-11-19")
IS_BLOCKS = [("2025-07-01", "2025-09-01"), ("2025-09-01", "2025-11-01"),
             ("2025-11-01", "2026-01-01"), ("2026-01-01", "2026-03-01")]
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "research")
os.makedirs(OUT, exist_ok=True)

_M = {}
def mkt(tf="M5"):
    if tf not in _M:
        _M[tf] = E.market(E.load(tf))
    return _M[tf]

def cut(t, period):
    a, b = period
    et = t.entry_time
    return t[(et >= pd.Timestamp(a, tz="UTC")) & (et < pd.Timestamp(b, tz="UTC"))]

def weeks(period):
    return (pd.Timestamp(period[1]) - pd.Timestamp(period[0])).days / 7.0

def score(t, period=IS, blocks=IS_BLOCKS):
    """Selection score: t-statistic of R in-sample, but only if the strategy
    trades >= 2 times a week and no 2-month block lost more than 6R."""
    tt = cut(t, period)
    s = M.stats(tt, weeks=weeks(period))
    if s["trades"] < 2 * weeks(period):
        return -99, s
    worst = min(cut(tt, b).r.sum() for b in blocks)
    s["worst_block"] = worst
    return (s["t_stat"] if worst > -6 else s["t_stat"] - 2), s

def row(name, t, period, risk=0.5):
    s = M.stats(cut(t, period), risk, weeks=weeks(period))
    return dict(name=name, **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()})
