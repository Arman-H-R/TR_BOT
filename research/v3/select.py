# -*- coding: utf-8 -*-
"""Candidate selection from the store - uses TRAIN and VALIDATION only.

robust score  = min(t_train, t_val)       an edge must show in both periods
profit score  = total_r_val, needing total_r_train > 0
Minimum sample: >= 40 train trades and >= 25 validation trades (about one a
week); below that a t-statistic of R is too noisy to rank on."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pandas as pd  # noqa: E402
from research.v3 import store  # noqa: E402

MIN_TRAIN, MIN_VAL = 40, 25


def wide(stages) -> pd.DataFrame:
    if isinstance(stages, str):
        stages = [stages]
    parts = [store.frame(s) for s in stages]
    df = pd.concat(parts, ignore_index=True)
    df = df[df.status == "ok"]
    cols = ["trades", "total_r", "t_stat", "pf", "max_dd_pct", "max_dd_r", "win_rate",
            "net_profit_pct", "expectancy", "per_week", "max_consec_losses", "pos_months", "months"]
    w = df.pivot_table(index=["exp_id", "stage", "label"], columns="split", values=cols, aggfunc="first")
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    w = w.reset_index()
    spec = df.drop_duplicates("exp_id").set_index("exp_id").spec
    w["spec"] = w.exp_id.map(spec)
    return w


def ranked(stages, how="robust", min_train=MIN_TRAIN, min_val=MIN_VAL, train="train") -> pd.DataFrame:
    w = wide(stages)
    if train != "train":                  # multi-year datasets: ltrain / htrain play the train role
        for c in [c for c in w.columns if c.endswith("_" + train)]:
            w[c[: -len(train)] + "train"] = w[c]
    w = w[(w.trades_train >= min_train) & (w.trades_val >= min_val)].copy()
    w["robust"] = w[["t_stat_train", "t_stat_val"]].min(axis=1)
    if how == "robust":
        return w.sort_values("robust", ascending=False)
    if how == "profit":
        return w[w.total_r_train > 0].sort_values("total_r_val", ascending=False)
    raise ValueError(how)


def best_specs(stages, n=3, how="robust", **kw):  # kw: train="ltrain" for stage 6
    r = ranked(stages, how, **kw).head(n)
    return [json.loads(s) for s in r.spec]
