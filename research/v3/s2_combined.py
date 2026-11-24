# -*- coding: utf-8 -*-
"""Stage 2 - combined parameter search on M5 (dataset A), Optuna TPE on TRAIN.
Two objectives: t-statistic of R (consistent edge) and total R with a
drawdown cap (raw profit). The v2 defaults are enqueued as trial 0."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from research.v3.search import run_study, strategy_space

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
V2 = dict(swing_lookback=2, ob_search_bars=20, ob_valid_bars=80, pullback_extra_bars=2,
          min_pullback_bars=3, ob_pick="last", ob_use_body=False, cancel_on_opposite_break=False,
          lq_tolerance_pips=10.0, lq_min_touches=2, lq_lookback_bars=80, lq_require_between_bos=True,
          lq_sweep_within_bars=0, entry_confirm_close=True, sl_buffer_pips=4.0, rr=4.0,
          be_trigger_r=2.0, be_cost_pips=2.0, trail_steps=False, min_sl_pips=4.0, max_sl_pips=150.0,
          session_start="08:00", session_end="17:00", friday_close="16:00", min_quality=1,
          max_trades_per_day=3, trend_filter="off", amd_mode="off")


def build(trial):
    return dict(cfg=strategy_space(trial, "M5"), arch=dict(setup="M5"))


if __name__ == "__main__":
    for obj in ("tstat", "total_r"):
        run_study(f"s2_m5_{obj}", build, N, objective=obj, enqueue=[V2], seed=11)
