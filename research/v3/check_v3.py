# -*- coding: utf-8 -*-
"""Sanity checks for the v3 evaluator: parity with the verified engine, the
execution re-mapping, resampling vs broker candles, and no look-ahead in the
higher-timeframe context."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import numpy as np, pandas as pd
from dataclasses import replace
import smc_core as core
from research import engine as E
from research.v2 import params_from_config
from research.v3 import core3 as C

# 1 parity with research/engine.py (which matches backtest.run 1:1)
t3 = C.evaluate(dict(dataset="A"))
m = E.market(E.load("M5")); c = E.candidates_fast(E.load("M5"), core.Config())
t2 = E.run(c, m, params_from_config(core.Config(), **E.COSTS["real"]))
assert len(t3) == len(t2) and np.allclose(t3.r.values, t2.r.values), (len(t3), len(t2))
print("1 parity ok", len(t3), round(t3.r.sum(), 2))

# 2 forcing the execution re-map with exec == setup changes nothing
sig_spec = dict(dataset="A", arch=dict(setup="M5", exec="M5", entry="none"))
cfg = core.Config(); p = params_from_config(cfg, **E.COSTS["real"])
mS = C.market("M5"); ok = E.eligible(c, mS, p); sig = E.select(c, ok); q = E.quality(sig, mS, p)
s2, q2 = C.to_exec(sig, q, mS, "M5", mS, "M5", {}, p)
assert (s2.i.values == sig.i.values).all()
print("2 exec remap identity ok")

# 3 resampled M15 from M5 == broker M15 candles (overlap period)
r15 = C.bars("M15", "A"); b15 = E.load("M15")
j = r15.index.intersection(b15.index)
diff = (r15.loc[j, ["open", "high", "low", "close"]] - b15.loc[j, ["open", "high", "low", "close"]]).abs()
print("3 M15 resample vs broker: candles", len(j), "max abs diff", diff.max().round(3).to_dict(),
      "share exact", round(float((diff.max(axis=1) < 0.011).mean()), 4))

# 4 no look-ahead: perturb the future, earlier context values must not change
ctx = C.htf_context("H1", "A")
df = C.bars("H1", "A"); k = len(df) // 2
C._BARS[("H1", "A")] = pd.concat([df.iloc[:k + 1], df.iloc[k + 1:] * 1.3 + 50])
C._CTX.clear()
ctx2 = C.htf_context("H1", "A")
for key in ("ema", "struct", "sweep_up", "sweep_dn"):
    assert (ctx[key][:k + 1] == ctx2[key][:k + 1]).all(), key
z1 = ctx["zones"][ctx["zones"][:, 0] <= ctx["close_t"][k].value]
z2 = ctx2["zones"][ctx2["zones"][:, 0] <= ctx2["close_t"][k].value]
assert np.allclose(z1[:, [0, 2, 3, 4]], z2[:, [0, 2, 3, 4]])
C._BARS[("H1", "A")] = df; C._CTX.clear()
print("4 HTF context has no look-ahead ok")

# 5 a context value stamped at close time is not visible before it
at = pd.DatetimeIndex([ctx["close_t"][100] - pd.Timedelta(minutes=1), ctx["close_t"][100]])
v = C._asof(ctx["close_t"], np.arange(len(ctx["close_t"])), at)
assert v[0] == 99 and v[1] == 100
print("5 as-of alignment ok")
