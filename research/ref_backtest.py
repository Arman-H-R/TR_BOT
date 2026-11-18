# -*- coding: utf-8 -*-
"""Authoritative backtest.run() trades for a few configs -> reports/research/ref_<name>.csv"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataclasses import replace
import pandas as pd
import backtest, smc_core as core
from research import engine as E

CFGS = {"shipped": {}, "trail_tp5": dict(trail_steps=True, rr=5.0)}

if __name__ == "__main__":
    names = sys.argv[1:] or list(CFGS)
    df = E.load("M5")[["open", "high", "low", "close"]]
    for name in names:
        cfg = replace(core.Config(), **CFGS[name])
        tr, _, _ = backtest.run(df, cfg, 0.30, balance=4700, spec=dict(stops_level=5, point=0.01))
        pd.DataFrame([x.__dict__ for x in tr]).to_csv(f"reports/research/ref_{name}.csv", index=False)
        print(name, len(tr), flush=True)
