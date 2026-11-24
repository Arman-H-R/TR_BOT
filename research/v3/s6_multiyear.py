# -*- coding: utf-8 -*-
"""
Stage 6 - multi-year optimisation (robustness across market regimes).

The optimiser's objective uses ONLY history before 2026-01-01:
  dataset L  M15 candles 2022-09 .. 2025-12  (ltrain, ~3.3 years)  -> M15 / M30 / H1 setups
  dataset H  H1 candles  2017-08 .. 2025-12  (htrain, ~8.4 years)  -> H1 / H4 setups
             (the broker's H1 file before mid-2017 is ~1 candle a day - unusable)
Validation (2026-01 .. 05) and test (2026-06 .. 11) are the same windows as in
every other stage; validation is only recorded here, test is untouched.
Per-year (L) and per-era (H) numbers are stored for every trial, so regime
consistency can be checked afterwards.

Limits (documented in the report):
  * M5 / M1 have no multi-year history at the broker -> not covered here
  * exits are simulated on the dataset's base candles (M15 / H1): if one candle
    touches both stop and target the stop is assumed first (conservative)
  * pip-distance parameters are fixed in price units; gold was ~3x cheaper in
    2017, so the same pips meant a relatively wider stop then
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from research.v3.search import run_study, strategy_space  # noqa: E402
from research.v3 import select  # noqa: E402
from research.v3.s4_mtf import builder as mtf_builder  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
PROCS = int(sys.argv[2]) if len(sys.argv) > 2 else 14

PLAN = [  # name, dataset, setup TF, execution TF, train split, min trades / week
    ("s6_L_M15", "L", "M15", "M15", "ltrain", 0.5),
    ("s6_L_M30", "L", "M30", "M15", "ltrain", 0.4),
    ("s6_L_H1", "L", "H1", "M15", "ltrain", 0.3),
    ("s6_H_H1", "H", "H1", "H1", "htrain", 0.2),
    ("s6_H_H4", "H", "H4", "H1", "htrain", 0.15),
]


def builder(setup, exec_tf):
    def build(trial):
        return dict(cfg=strategy_space(trial, setup), arch=dict(setup=setup, exec=exec_tf), tf=setup)
    return build


if __name__ == "__main__":
    for name, ds, setup, ex, tr, mpw in PLAN:
        run_study(name, builder(setup, ex), N, objective="tstat", seed=61, dataset=ds,
                  obj_split=tr, min_per_week=mpw, procs=PROCS)
    # multi-year MTF: the best multi-year M15 setup + HTF context, re-optimised
    best = select.best_specs("s6_L_M15", n=1, train="ltrain")
    if best:
        b = mtf_builder("M15", best[0]["cfg"])

        def build(trial):
            sp = b(trial)
            sp["arch"]["exec"] = "M15"
            if sp["arch"].get("entry", "none") != "none":
                sp["arch"]["entry_wait"] = max(sp["arch"].get("entry_wait", 30), 30)
            return sp
        run_study("s6_L_M15_mtf", build, N, objective="tstat", seed=67, dataset="L",
                  obj_split="ltrain", min_per_week=0.4, procs=PROCS)
