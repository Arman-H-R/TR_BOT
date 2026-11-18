# -*- coding: utf-8 -*-
"""
Does research/engine.py reproduce backtest.run()? Same candles, several configs,
including the v2 rules (gap fills, Friday close, H1 trend, close-out confirm).

    python research/check_engine.py            # all variants (slow: one backtest.run each)
    python research/check_engine.py shipped    # one
"""
import os
import sys
import time
from datetime import time as dtime
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd

import backtest
import smc_core as core
from research import cache, engine as E

V1 = dict(trend_filter="off", entry_confirm_close=False, friday_close=None, min_quality=1, be_trigger_r=1.0)
VARIANTS = {
    # name: (Config changes, engine Params changes, backtest gap_fill)
    "v1 shipped, old fills": (V1, dict(gap_fill=False), False),
    "v1 shipped, gap fills": (V1, dict(), True),
    "friday+close-out+q2": (dict(V1, friday_close=dtime(16, 0), entry_confirm_close=True, min_quality=2),
                            dict(friday_close=960, confirm="close_out", min_quality=2), True),
    "H1 trend + trail TP5": (dict(V1, trend_filter="H1", trail_steps=True, rr=5.0),
                             dict(trend_tf="h1", trend_mode="with", trail_start=2.0, rr=5.0), True),
    "v2 default": ({}, "V2", True),
}


def main(names):
    df = E.load("M5")
    m = E.market(df)
    ok_all = True
    for name in names:
        ccfg, pp, gap = VARIANTS[name]
        cfg = replace(core.Config(), **ccfg)
        c = cache.get(cfg)
        if pp == "V2":
            from research.v2 import params_from_config
            p = params_from_config(cfg)
        else:
            p = replace(E.Params(), **pp)
        t0 = time.time()
        ref, _, _ = backtest.run(df[["open", "high", "low", "close"]], cfg, 0.30, balance=4700,
                                 spec=dict(stops_level=5, point=0.01), gap_fill=gap)
        ref = pd.DataFrame([x.__dict__ for x in ref])
        got = E.run(c, m, p)
        a = set(pd.to_datetime(ref.entry_time, utc=True)) if len(ref) else set()
        b = set(got.entry_time)
        common = sorted(a & b)
        dr = 0.0
        if common:
            rr = ref.set_index(pd.to_datetime(ref.entry_time, utc=True)).r.loc[common].to_numpy()
            gg = got.set_index("entry_time").r.loc[common].to_numpy()
            dr = float(np.abs(rr - gg).max())
        same = (a == b) and dr < 1e-6
        ok_all &= same
        print(f"{name:<24} backtest {len(a):>4} engine {len(b):>4} same {len(common):>4} "
              f"extra {len(b - a)} missing {len(a - b)} max|dR| {dr:.2e} "
              f"sumR {ref.r.sum() if len(ref) else 0:+.2f} vs {got.r.sum():+.2f}  "
              f"{'PASS' if same else 'DIFFERS'}  ({time.time() - t0:.0f}s)", flush=True)
    print("PASS" if ok_all else "DIFFERS")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or list(VARIANTS)))
