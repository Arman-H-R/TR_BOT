# -*- coding: utf-8 -*-
"""
Stage 1 - one-at-a-time sweep of all 71 Config parameters (M5, dataset A,
realistic costs, TRAIN + VALIDATION only; the TEST split is not touched).

Every value is a change from the shipped v2 defaults with everything else
fixed. Parameters the fast engine cannot model run through the slow reference
simulator backtest.run (kind="ref", spread-only costs) against a reference
baseline computed the same way. Dependent parameters are swept with their
switch turned on (e.g. trail_* with trail_steps=True), and the switch-on
baseline is stored as well.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from research.v3.runner import run_specs  # noqa: E402

T = lambda h, m=0: f"{h:02d}:{m:02d}"

# param: (values, base changes that must be on for the param to matter)
FAST = {
    # structure (stage-1 candidates)
    "swing_lookback": ([1, 2, 3, 4, 5, 6], {}),
    "bos_max_age": ([20, 40, 60, 80, 120, 160, 240], {}),
    "ob_search_bars": ([5, 10, 15, 20, 30, 40], {}),
    "ob_pick": (["last", "first", "extreme", "biggest"], {}),
    "ob_use_body": ([False, True], {}),
    "sl_use_wick": ([True, False], {"ob_use_body": True}),
    "ob_require_small_body": ([False, True], {}),
    "ob_max_body_ratio": ([0.2, 0.35, 0.5, 0.7, 0.85, 1.0], {"ob_require_small_body": True}),
    "ob_valid_bars": ([20, 40, 60, 80, 120, 160, 240], {}),
    "max_impulse_bars": ([3, 5, 10, 15, 20, 30, 40], {}),
    "min_pullback_bars": ([0, 1, 2, 3, 5, 8, 12], {}),
    "pullback_extra_bars": ([0, 1, 2, 3, 5, 8], {}),
    "cancel_on_opposite_break": ([False, True], {}),
    "lq_tolerance_pips": ([2, 5, 8, 10, 15, 20, 30], {}),
    "lq_min_touches": ([1, 2, 3, 4], {}),
    "lq_lookback_bars": ([20, 40, 80, 120, 200], {}),
    "lq_require_between_bos": ([True, False], {}),
    "lq_max_distance_pips": ([0, 30, 60, 100, 200, 400], {}),
    "lq_sweep_within_bars": ([0, 3, 5, 10, 20, 40], {}),
    "lq_allow_pre_ob": ([False, True], {}),
    # entry / risk / exits
    "entry_confirm_close": ([True, False], {}),
    "sl_buffer_pips": ([0, 1, 2, 4, 6, 8, 12, 20], {}),
    "rr": ([1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 8], {}),
    "be_trigger_r": ([0.5, 1, 1.5, 2, 2.5, 3, 99], {}),   # 99 = never
    "be_cost_pips": ([0, 1, 2, 4, 6, 10], {}),
    "trail_steps": ([False, True], {}),
    "trail_start_r": ([1, 1.5, 2, 2.5, 3], {"trail_steps": True}),
    "trail_gap_r": ([0.5, 1, 1.5, 2], {"trail_steps": True}),
    "min_sl_pips": ([0, 4, 10, 20, 30, 50], {}),
    "max_sl_pips": ([40, 60, 80, 100, 150, 200, 300, 1000], {}),
    # session
    "session_start": ([T(h) for h in (2, 3, 5, 7, 8, 9, 10)], {}),
    "session_end": ([T(h) for h in (11, 12, 13, 14, 15, 16, 17, 19, 21)], {}),
    "trade_weekends": ([False, True], {}),
    "close_at_session_end": ([False, True], {}),
    "friday_close": ([None, T(11), T(12), T(14), T(16)], {}),
    # quality
    "quality_small_ob": ([0, 1, 2, 3], {}),
    "quality_lq_strong": ([0, 1, 2, 3], {}),
    "quality_sharp_impulse": ([0, 1, 2, 3], {}),
    "quality_slow_return": ([0, 1, 2, 3], {}),
    "quality_first_hour": ([0, 1, 2, 3], {}),
    "ob_small_pips": ([10, 15, 20, 30, 50, 80], {}),
    "lq_strong_touches": ([2, 3, 4, 5], {}),
    "sharp_impulse_bars": ([1, 2, 3, 5], {}),
    "slow_return_ratio": ([2, 4, 6, 8, 12, 16], {}),
    "first_hour_start": ([7, 8, 9], {}),
    "first_hour_end": ([9, 10, 11], {}),
    "min_quality": ([0, 1, 2, 3, 4, 5, 6], {}),
    # daily limits
    "max_trades_per_day": ([1, 2, 3, 4, 5, 10], {}),
    "extra_trades_if_quality": ([0, 1, 2, 4], {}),
    "good_quality": ([3, 4, 5, 6, 7, 99], {}),
    # filters that are off by default
    "trend_filter": (["off", "H1", "H4"], {}),
    "amd_mode": (["off", "filter"], {}),
}
# Config fields the fast engine does not read -> slow reference simulator
REF = {
    "entry_on_retouch": ([True, False], {}),
    "fail_on_touch": ([False, True], {}),
    "second_open_start": ([-1, 13, 14], {"second_open_end": 16}),
    "second_open_end": ([15, 16, 17], {"second_open_start": 13}),
    "amd_mode": (["off", "filter", "quality"], {}),
    "amd_accum_start": ([T(0), T(1), T(2), T(3)], {"amd_mode": "quality"}),
    "amd_accum_end": ([T(6), T(7), T(8)], {"amd_mode": "quality"}),
    "amd_manip_end": ([T(9), T(10), T(11), T(12)], {"amd_mode": "quality"}),
    "amd_reclaim": ([True, False], {"amd_mode": "quality"}),
    "amd_min_range_pips": ([10, 20, 40, 80], {"amd_mode": "quality"}),
    "amd_quality": ([1, 2, 3, 5], {"amd_mode": "quality"}),
    "utc_start": ([T(13), T(12)], {}),       # fallback only - expected no effect
    "utc_end": ([T(21), T(22)], {}),
}
# trend_ema: the H1 EMA span; modelled through the v3 HTF context (closed H1
# candles, close vs EMA) - the same definition as smc_core.trend_series
TREND_EMA = [20, 50, 100, 200]
# not optimisable: symbol, timeframe (stage 4 = timeframe study), pip_size,
# contract_size (instrument constants), risk_percent (money management - R per
# trade does not depend on it; see s1 report: profit/DD vs risk)


def specs():
    out = [dict(label="baseline", cfg={}, dataset="A", costs="real")]
    for name, (vals, base) in FAST.items():
        if base:
            out.append(dict(label=f"{name}=<switch on baseline>", cfg=dict(base), dataset="A",
                            costs="real", param=name))
        for v in vals:
            out.append(dict(label=f"{name}={v}", cfg={**base, name: v}, dataset="A",
                            costs="real", param=name, value=str(v)))
    for span in TREND_EMA:
        out.append(dict(label=f"trend_ema={span}", cfg={"trend_filter": "off"}, dataset="A",
                        costs="real", param="trend_ema", value=str(span),
                        arch=dict(setup="M5", ctx_tf="H1", ctx=["ema"], ctx_ema=span)))
    out.append(dict(label="baseline", kind="ref", cfg={}, dataset="A", costs="base"))
    for name, (vals, base) in REF.items():
        if base:
            out.append(dict(label=f"{name}=<switch on baseline>", kind="ref", cfg=dict(base),
                            dataset="A", costs="base", param=name))
        for v in vals:
            out.append(dict(label=f"{name}={v}", kind="ref", cfg={**base, name: v}, dataset="A",
                            costs="base", param=name, value=str(v)))
    return out


if __name__ == "__main__":
    df = run_specs(specs(), "s1_oat")
    print(df.status.value_counts().to_string())
