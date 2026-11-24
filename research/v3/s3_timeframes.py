# -*- coding: utf-8 -*-
"""
Stage 3 - the complete strategy on each single timeframe (dataset A, the same
train/val split as M5, fills and exits on M5 candles for every setup TF).

3a  defaults on every TF: the v2 Config as is (candle counts unchanged) and
    with pip distances scaled by sqrt(TF minutes / 5)
3b  per-TF re-optimisation: Optuna on TRAIN (t-statistic), 1500 trials per TF
    (M5 = stage 2, not repeated)
M1 has only 3.5 months of data, all inside the TEST period, so it is not
optimised; it is evaluated as a diagnostic in s9_final.py.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from research.v3.runner import run_specs  # noqa: E402
from research.v3.search import run_study, strategy_space  # noqa: E402
from research.v3 import core3 as C  # noqa: E402

TFS = ["M15", "M30", "H1", "H4"]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
PIPS = ["sl_buffer_pips", "lq_tolerance_pips", "min_sl_pips", "max_sl_pips", "ob_small_pips"]


def defaults():
    out = []
    import smc_core as core
    base = core.Config()
    for tf in ["M5"] + TFS:
        f = math.sqrt(C.MIN[tf] / 5)
        out.append(dict(label=f"{tf} defaults", cfg={}, arch=dict(setup=tf, exec="M5"),
                        dataset="A", costs="real", tf=tf))
        out.append(dict(label=f"{tf} defaults, pips x{f:.2f}",
                        cfg={k: round(getattr(base, k) * f, 1) for k in PIPS},
                        arch=dict(setup=tf, exec="M5"), dataset="A", costs="real", tf=tf))
        # the same clock time instead of the same candle count
        g = 5 / C.MIN[tf]
        bars = dict(ob_valid_bars=max(5, round(80 * g)), lq_lookback_bars=max(10, round(80 * g)))
        out.append(dict(label=f"{tf} defaults, pips x{f:.2f}, same clock-time windows",
                        cfg={**{k: round(getattr(base, k) * f, 1) for k in PIPS}, **bars},
                        arch=dict(setup=tf, exec="M5"), dataset="A", costs="real", tf=tf))
    return out


def builder(tf):
    def build(trial):
        return dict(cfg=strategy_space(trial, tf), arch=dict(setup=tf, exec="M5"), tf=tf)
    return build


def record_m1_skip():
    """M1 is not silently dropped: the decision is stored as an experiment."""
    from research.v3 import store
    con = store.connect()
    spec = dict(stage="s3_M1", label="M1 optimisation skipped", arch=dict(setup="M1"), dataset="M1")
    store.save(con, spec, {}, 0.0, status="skipped",
               error=("M1 history from the broker: 2026-08-07 .. 2026-11-18 only (100 000 candles). "
                      "All of it lies inside the TEST split (2026-06-01 ..), so optimising on it would "
                      "leak the final test. M1 is evaluated once, as a diagnostic, in s9_final.py "
                      "(same rules on M1 candles, and M1 execution of the M5 finalists)."))
    con.commit()
    con.close()


if __name__ == "__main__":
    record_m1_skip()
    run_specs(defaults(), "s3_tf_defaults")
    for tf in TFS:
        run_study(f"s3_{tf}_tstat", builder(tf), N, objective="tstat", seed=23)
