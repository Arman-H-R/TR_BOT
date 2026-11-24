# -*- coding: utf-8 -*-
"""
research/v3/core3.py - data, splits and the multi-timeframe (MTF) evaluator.

Nothing here changes smc_core.py: the strategy logic (swings, BOS, order block,
liquidity, pullback) is smc_core's, run through research/engine.py's exact fast
path (candidates_fast + eligible/select/simulate, verified 1:1 against
backtest.run). What v3 adds:

* every timeframe is built from the finest base data by resampling
  (label = candle open, closed on the left); a candle of period P labelled t is
  only USED from t + P on (its close) - never while it is still forming;
* role-based architecture
    context TF  : directional / POI confirmation from CLOSED higher-TF candles
                  (EMA trend, HTF BOS structure, HTF order-block zone, HTF sweep)
    setup TF    : swing + BOS + OB + impulse + pullback + liquidity (smc_core)
    entry TF    : optional lower-TF confirmation after the setup touch
                  (close back out of the zone / micro break of structure)
    execution TF: fills and exits simulated on the finest candles available
* a spec dict fully describes one experiment; evaluate(spec) returns the trades.

Datasets (what the broker history allows; MT5 keeps 100 000 candles per TF):
    A   base M5   2025-06-23 .. 2026-11-18   main research data
    L   base M15  2022-08-11 .. 2026-11-18   long-history / regime check (M15+)
    M1  base M1   2026-08-07 .. 2026-11-18   only 3.5 months - diagnostic only
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import replace, fields

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import smc_core as core  # noqa: E402
from research import engine as E  # noqa: E402
from research import metrics as M  # noqa: E402
from research.v2 import params_from_config  # noqa: E402

MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
BASE = {"A": "M5", "L": "M15", "M1": "M1", "H": "H1"}

# chronological splits. TEST is touched only by the final stage (s9_final.py).
SPLITS = {
    "A": {"train": ("2025-07-01", "2026-01-01"),
          "val": ("2026-01-01", "2026-06-01"),
          "test": ("2026-06-01", "2026-11-19")},
    "L": {"y2022": ("2022-09-01", "2023-01-01"), "y2023": ("2023-01-01", "2024-01-01"),
          "y2024": ("2024-01-01", "2025-01-01"), "y2025": ("2025-01-01", "2026-01-01"),
          "y2026": ("2026-01-01", "2026-11-19")},
    "M1": {"m1win": ("2026-08-10", "2026-11-19")},
}
# walk-forward folds inside TRAIN+VAL only: 4 months in-sample -> next month out
_WF_OOS = ["2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01", "2026-03-01",
           "2026-04-01", "2026-05-01", "2026-06-01"]
for _k in range(len(_WF_OOS) - 1):
    _a = (pd.Timestamp(_WF_OOS[_k]) - pd.DateOffset(months=4)).strftime("%Y-%m-%d")
    SPLITS["A"][f"wf{_k + 1}_is"] = (_a, _WF_OOS[_k])
    SPLITS["A"][f"wf{_k + 1}_oos"] = (_WF_OOS[_k], _WF_OOS[_k + 1])
WF_FOLDS = len(_WF_OOS) - 1
# two-month blocks of TRAIN+VAL, for consistency checks
for _a, _b in [("2025-07-01", "2025-09-01"), ("2025-09-01", "2025-11-01"), ("2025-11-01", "2026-01-01"),
               ("2026-01-01", "2026-03-01"), ("2026-03-01", "2026-06-01")]:
    SPLITS["A"][f"blk_{_a[:7]}"] = (_a, _b)
SPLITS["A"]["trainval"] = ("2025-07-01", "2026-06-01")
SPLITS["A"]["full"] = ("2025-07-01", "2026-11-19")
SPLITS["A"]["last12"] = ("2025-11-19", "2026-11-19")      # the user's reporting window
SPLITS["L"]["full"] = ("2022-09-01", "2026-11-19")
# multi-year datasets (stage 6): the optimiser trains on everything BEFORE 2026;
# validation and test are exactly the dataset-A windows, never trained on
SPLITS["L"]["ltrain"] = ("2022-09-01", "2026-01-01")
SPLITS["L"]["val"] = SPLITS["A"]["val"]
SPLITS["L"]["test"] = SPLITS["A"]["test"]
# broker H1 history before mid-2017 is ~1 candle per day (not real H1) -> H starts 2017-08
SPLITS["H"] = {"htrain": ("2017-08-01", "2026-01-01"), "val": SPLITS["A"]["val"],
               "test": SPLITS["A"]["test"], "full": ("2017-08-01", "2026-11-19"),
               "e2017_19": ("2017-08-01", "2020-01-01"), "e2020_21": ("2020-01-01", "2022-01-01"),
               "e2022_23": ("2022-01-01", "2024-01-01"), "e2024_25": ("2024-01-01", "2026-01-01")}
SEARCH_SPLITS = {"A": ("train", "val"), "L": ("y2022", "y2023", "y2024", "y2025", "y2026"),
                 "M1": ("m1win",)}
SEARCH_SPLITS["L"] = ("ltrain", "val", "y2022", "y2023", "y2024", "y2025")
SEARCH_SPLITS["H"] = ("htrain", "val", "e2017_19", "e2020_21", "e2022_23", "e2024_25")
RISK = 1.0          # % per trade - what the demo bot runs
CFG_FIELDS = {f.name for f in fields(core.Config)}

_BARS: dict = {}
_MKT: dict = {}


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def bars(tf: str, ds: str = "A") -> pd.DataFrame:
    key = (tf, ds)
    if key in _BARS:
        return _BARS[key]
    base = BASE[ds]
    df = E.load(base)
    if MIN[tf] < MIN[base]:
        raise ValueError(f"{tf} is finer than the {ds} base {base}")
    if tf != base:
        rule = f"{MIN[tf]}min"
        g = df.resample(rule, label="left", closed="left")
        df = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(),
                           "close": g.close.last(), "spread_px": g.spread_px.last()}).dropna()
        # drop a last candle that had not closed when the data ends
        last_base = E.load(base).index[-1] + pd.Timedelta(minutes=MIN[base])
        df = df[df.index + pd.Timedelta(minutes=MIN[tf]) <= last_base]
    _BARS[key] = df
    return df


def market(tf: str, ds: str = "A") -> E.Market:
    key = (tf, ds)
    if key not in _MKT:
        _MKT[key] = E.market(bars(tf, ds))
    return _MKT[key]


# --------------------------------------------------------------------------- #
# candidates cache (per TF, dataset and structure parameters)
# --------------------------------------------------------------------------- #
from research.cache import STRUCT  # noqa: E402
CACHE = os.path.join(HERE, "cache")


def make_cfg(changes: dict) -> core.Config:
    bad = set(changes) - CFG_FIELDS
    if bad:
        raise KeyError(f"not Config fields: {bad}")
    ch = dict(changes)
    for k in ("session_start", "session_end", "friday_close", "amd_accum_start",
              "amd_accum_end", "amd_manip_end", "utc_start", "utc_end"):
        if isinstance(ch.get(k), str):
            h, m = ch[k].split(":")
            ch[k] = pd.Timestamp(2000, 1, 1, int(h), int(m)).time()
    return replace(core.Config(), **ch)


def candidates(cfg: core.Config, tf: str, ds: str) -> pd.DataFrame:
    os.makedirs(CACHE, exist_ok=True)
    d = {k: getattr(cfg, k) for k in STRUCT}
    d.update(tf=tf, ds=ds)
    key = hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]
    path = os.path.join(CACHE, key + ".pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    c = E.candidates_fast(bars(tf, ds), cfg)
    tmp = f"{path}.{os.getpid()}.tmp"
    c.to_pickle(tmp)
    os.replace(tmp, path)          # atomic: parallel workers never read a half file
    return c


# --------------------------------------------------------------------------- #
# higher-timeframe context (closed candles only)
# --------------------------------------------------------------------------- #
_CTX: dict = {}


def _asof(times_avail: pd.DatetimeIndex, values: np.ndarray, at: pd.DatetimeIndex, fill=0):
    """values[j] becomes known at times_avail[j]; return the latest known value at
    each time in `at` (fill if none yet)."""
    pos = np.searchsorted(times_avail.asi8, at.asi8, side="right") - 1
    out = np.where(pos >= 0, values[np.clip(pos, 0, None)], fill)
    return out


def htf_context(tf: str, ds: str, k: int = 2, ema: int = 50, ob_valid: int = 40,
                sweep_bars: int = 6):
    """Per closed HTF candle: EMA side, structure (last BOS) direction, sweep flags,
    plus the list of HTF order-block zones. All stamped with the candle CLOSE time."""
    key = (tf, ds, k, ema, ob_valid, sweep_bars)
    if key in _CTX:
        return _CTX[key]
    df = bars(tf, ds)
    P = pd.Timedelta(minutes=MIN[tf])
    close_t = df.index + P
    c, h, l = (df[x].to_numpy(float) for x in ("close", "high", "low"))
    n = len(df)
    ema_side = np.sign(c - pd.Series(c).ewm(span=ema, adjust=False).mean().to_numpy())
    b = core.Bars.from_dataframe(df[["open", "high", "low", "close"]])
    highs, lows = core.find_swings(b, k)
    events = E._global_breaks(b, highs, lows, k, need=n + 10)
    struct = np.zeros(n)
    last = 0
    ev_at = {}
    for e in events:
        ev_at[e.break_index] = e.direction
    for i in range(n):
        last = ev_at.get(i, last)
        struct[i] = last
    # sweeps: candle trades through the last confirmed swing and closes back
    sw_up = np.zeros(n, bool)   # bullish: sell-side liquidity (swing low) taken
    sw_dn = np.zeros(n, bool)
    hi_p = sorted((p[0], p[1]) for p in highs)
    lo_p = sorted((p[0], p[1]) for p in lows)
    hj = lj = 0
    last_hi = last_lo = None
    for i in range(n):
        while hj < len(hi_p) and hi_p[hj][0] + k < i:
            last_hi = hi_p[hj][1]
            hj += 1
        while lj < len(lo_p) and lo_p[lj][0] + k < i:
            last_lo = lo_p[lj][1]
            lj += 1
        if last_lo is not None and l[i] < last_lo < c[i]:
            sw_up[i] = True
        if last_hi is not None and h[i] > last_hi > c[i]:
            sw_dn[i] = True
    roll = lambda x: pd.Series(x.astype(float)).rolling(sweep_bars, min_periods=1).max().to_numpy() > 0
    # HTF order blocks: zone registered at the close of the break candle, alive for
    # ob_valid candles or until a close beyond its far edge
    cfg = core.Config()
    zones = []
    for e in events:
        ob = core.find_order_block(b, e, cfg)
        if ob is None:
            continue
        end = min(e.break_index + ob_valid, n - 1)
        for j in range(e.break_index + 1, end + 1):
            if (ob.direction > 0 and c[j] < ob.bottom) or (ob.direction < 0 and c[j] > ob.top):
                end = j
                break
        zones.append((close_t[e.break_index].value, close_t[end].value, ob.direction,
                      ob.bottom, ob.top))
    zones = np.array(zones, float) if zones else np.zeros((0, 5))
    out = dict(close_t=close_t, ema=ema_side, struct=struct, sweep_up=roll(sw_up),
               sweep_dn=roll(sw_dn), zones=zones)
    _CTX[key] = out
    return out


def context_mask(c: pd.DataFrame, mS: E.Market, setup_tf: str, ds: str, arch: dict) -> np.ndarray:
    """Higher-timeframe confirmations. arch["ctx"] items are "kind" (uses
    arch["ctx_tf"]) or [kind, tf]; kind in ema | bos | liq | ob. ctx_mode "all"
    makes every item mandatory, "any" needs one of them (optional confirmations)."""
    need = arch.get("ctx") or []
    if not need or not len(c):
        return np.ones(len(c), bool)
    # the setup candle i is known at its close
    T = pd.DatetimeIndex(mS.time[c.i.to_numpy()]) + pd.Timedelta(minutes=MIN[setup_tf])
    d = c.d.to_numpy()
    checks = []
    for item in need:
        kind, tf = (item, arch["ctx_tf"]) if isinstance(item, str) else (item[0], item[1])
        ctx = htf_context(tf, ds, arch.get("ctx_k", 2), arch.get("ctx_ema", 50),
                          arch.get("ctx_ob_valid", 40), arch.get("ctx_sweep_bars", 6))
        if kind == "ema":
            checks.append(_asof(ctx["close_t"], ctx["ema"], T) == d)
        elif kind == "bos":
            checks.append(_asof(ctx["close_t"], ctx["struct"], T) == d)
        elif kind == "liq":
            up = _asof(ctx["close_t"], ctx["sweep_up"].astype(int), T)
            dn = _asof(ctx["close_t"], ctx["sweep_dn"].astype(int), T)
            checks.append(np.where(d > 0, up, dn) > 0)
        elif kind == "ob":
            z = ctx["zones"]
            edge = np.where(d > 0, c.ob_top.to_numpy(), c.ob_bottom.to_numpy())
            tol = arch.get("ctx_ob_tol", 0.0)
            tv = T.asi8.astype(float)
            hit = np.zeros(len(c), bool)
            for zs, ze, zd, zb, zt in z:
                hit |= (tv >= zs) & (tv <= ze) & (d == zd) & (edge >= zb - tol) & (edge <= zt + tol)
            checks.append(hit)
        else:
            raise ValueError(kind)
    st = np.vstack(checks)
    return st.all(0) if arch.get("ctx_mode", "all") == "all" else st.any(0)


# --------------------------------------------------------------------------- #
# lower-TF entry confirmation + mapping onto the execution candles
# --------------------------------------------------------------------------- #
def to_exec(sig: pd.DataFrame, q: np.ndarray, mS: E.Market, setup_tf: str, mX: E.Market,
            exec_tf: str, arch: dict, p: E.Params):
    """Re-index the signals onto the execution candles. Entry is the close of the
    execution candle that ends with the setup candle (= the setup close), or the
    close of the first lower-TF confirmation candle within `entry_wait` minutes."""
    if not len(sig):
        return sig, q
    PS, PX = pd.Timedelta(minutes=MIN[setup_tf]), pd.Timedelta(minutes=MIN[exec_tf])
    T = pd.DatetimeIndex(mS.time[sig.i.to_numpy()]) + PS
    ix = np.searchsorted(mX.time.asi8, (T - PX).asi8, side="right") - 1
    mode = arch.get("entry", "none")
    wait = int(arch.get("entry_wait", 0) / MIN[exec_tf])
    keep = np.ones(len(sig), bool)
    new_i = ix.copy()
    if mode != "none":
        d_arr = sig.d.to_numpy()
        stop = sig.stop_edge.to_numpy() - d_arr * p.sl_buffer_pips * p.pip
        top, bot = sig.ob_top.to_numpy(), sig.ob_bottom.to_numpy()
        start_touch = np.searchsorted(mX.time.asi8, mS.time[sig.i.to_numpy()].asi8, side="left")
        n = len(mX)
        for k in range(len(sig)):
            d = d_arr[k]
            a = ix[k]
            ref = (mX.h[start_touch[k]:a + 1].max() if d > 0 else mX.l[start_touch[k]:a + 1].min())
            found = -1
            for j in range(a + 1, min(a + 1 + wait, n)):
                if (mX.l[j] <= stop[k]) if d > 0 else (mX.h[j] >= stop[k]):
                    break                                       # invalidated first
                if mode == "close_out":
                    if (mX.c[j] > top[k]) if d > 0 else (mX.c[j] < bot[k]):
                        found = j
                        break
                elif mode == "choch":
                    if (mX.c[j] > ref) if d > 0 else (mX.c[j] < ref):
                        found = j
                        break
                    ref = max(ref, mX.h[j]) if d > 0 else min(ref, mX.l[j])
                elif mode == "color":
                    if (mX.c[j] > mX.o[j]) if d > 0 else (mX.c[j] < mX.o[j]):
                        found = j
                        break
            if found < 0:
                keep[k] = False
            new_i[k] = found
    out = sig.assign(i=new_i)[keep]
    q = np.asarray(q)[keep]
    order = np.argsort(out.i.to_numpy(), kind="stable")
    return out.iloc[order], q[order]


# --------------------------------------------------------------------------- #
# one experiment
# --------------------------------------------------------------------------- #
def costs_of(name: str) -> dict:
    return dict(E.COSTS[name])


def evaluate(spec: dict) -> pd.DataFrame:
    """spec: dataset, arch{setup, exec, entry, entry_wait, ctx_tf, ctx[], ...},
    cfg{Config changes}, extra{engine Params overrides}, costs."""
    ds = spec.get("dataset", "A")
    arch = spec.get("arch", {})
    setup_tf = arch.get("setup", "M5")
    exec_tf = arch.get("exec", BASE[ds])
    cfg = make_cfg(spec.get("cfg", {}))
    p = params_from_config(cfg, **costs_of(spec.get("costs", "real")))
    p = replace(p, **spec.get("extra", {}))
    if "weekdays" not in spec.get("extra", {}):
        p = replace(p, weekdays=(0, 1, 2, 3, 4, 5, 6) if cfg.trade_weekends else (0, 1, 2, 3, 4))
    c = candidates(cfg, setup_tf, ds)
    mS = market(setup_tf, ds)
    if not len(c):                 # structure rules that never produce a setup -> 0 trades
        return E.simulate(c, mS, p, np.zeros(0, int))
    ok =E.eligible(c, mS, p) & context_mask(c, mS, setup_tf, ds, arch)
    sig = E.select(c, ok)
    q = E.quality(sig, mS, p) if len(sig) else np.zeros(0, int)
    if exec_tf == setup_tf and arch.get("entry", "none") == "none":
        return E.simulate(sig, mS, p, q)
    mX = market(exec_tf, ds)
    sig, q = to_exec(sig, q, mS, setup_tf, mX, exec_tf, arch, p)
    return E.simulate(sig, mX, p, q)


def cut(t: pd.DataFrame, period) -> pd.DataFrame:
    a, b = period
    et = t.entry_time
    return t[(et >= pd.Timestamp(a, tz="UTC")) & (et < pd.Timestamp(b, tz="UTC"))]


def metrics(t: pd.DataFrame, period, risk: float = RISK) -> dict:
    tt = cut(t, period)
    weeks = (pd.Timestamp(period[1]) - pd.Timestamp(period[0])).days / 7.0
    s = M.stats(tt, risk, weeks=weeks)
    r = tt.r.to_numpy(float) if len(tt) else np.zeros(0)
    return dict(trades=s["trades"], wins=int((r > 0.05).sum()), losses=int((r < -0.05).sum()),
                be=int(((r >= -0.05) & (r <= 0.05)).sum()), win_rate=s.get("win", 0),
                net_profit_pct=s.get("ret", 0), total_r=s.get("total_r", 0),
                pf=min(s.get("pf", 0), 99.0), expectancy=s.get("avg_r", 0),
                avg_win=s.get("avg_win", 0), avg_loss=s.get("avg_loss", 0),
                max_dd_pct=s.get("dd_pct", 0), max_dd_r=s.get("dd_r", 0),
                max_consec_losses=s.get("max_losses", 0), avg_r=s.get("avg_r", 0),
                per_week=s.get("per_week", 0), t_stat=s.get("t_stat", 0),
                pos_months=s.get("pos_months", 0), months=s.get("months", 0),
                buys=s.get("buys", 0), sells=s.get("sells", 0), tp=s.get("tp", 0), sl=s.get("sl", 0))


def run_and_store(spec: dict, con, splits=None) -> dict:
    """Evaluate one spec and write it to the store; returns {split: metrics}."""
    import time
    import traceback
    from research.v3 import store
    ds = spec.get("dataset", "A")
    splits = splits or SEARCH_SPLITS[ds]
    t0 = time.time()
    try:
        t = evaluate(spec)
        rows = {s: (f"{SPLITS[ds][s][0]}..{SPLITS[ds][s][1]}", metrics(t, SPLITS[ds][s]))
                for s in splits}
        store.save(con, spec, rows, time.time() - t0)
        return {s: m for s, (_, m) in rows.items()}
    except Exception:
        store.save(con, spec, {}, time.time() - t0, status="error", error=traceback.format_exc())
        return {}
