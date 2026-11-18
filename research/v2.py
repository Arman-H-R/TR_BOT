# -*- coding: utf-8 -*-
"""Map a smc_core.Config onto the research engine's Params, so the engine can
evaluate exactly what the bot is configured to trade."""
import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import smc_core as core  # noqa: E402
from research import engine as E  # noqa: E402


def _mins(t):
    return t.hour * 60 + t.minute


def params_from_config(cfg: core.Config, **costs) -> E.Params:
    p = E.Params(
        sess_start=_mins(cfg.session_start), sess_end=_mins(cfg.session_end),
        q_small=cfg.quality_small_ob, q_lq=cfg.quality_lq_strong,
        q_sharp=cfg.quality_sharp_impulse, q_slow=cfg.quality_slow_return,
        q_first=cfg.quality_first_hour, ob_small_pips=cfg.ob_small_pips,
        lq_strong=cfg.lq_strong_touches, sharp_bars=cfg.sharp_impulse_bars,
        slow_ratio=cfg.slow_return_ratio, first_start=cfg.first_hour_start * 60,
        first_end=cfg.first_hour_end * 60, min_quality=cfg.min_quality,
        trend_tf=cfg.trend_filter.lower() if cfg.trend_filter != "off" else "",
        trend_mode="with" if cfg.trend_filter != "off" else "off",
        confirm="close_out" if cfg.entry_confirm_close else "none",
        amd_mode=cfg.amd_mode,
        sl_buffer_pips=cfg.sl_buffer_pips, min_sl_pips=cfg.min_sl_pips,
        max_sl_pips=cfg.max_sl_pips, rr=cfg.rr, be_r=cfg.be_trigger_r,
        be_cost_pips=cfg.be_cost_pips,
        trail_start=cfg.trail_start_r if cfg.trail_steps else 0.0, trail_gap=cfg.trail_gap_r,
        close_session_end=cfg.close_at_session_end,
        friday_close=_mins(cfg.friday_close) if cfg.friday_close is not None else 0,
        max_per_day=cfg.max_trades_per_day, extra_q=cfg.extra_trades_if_quality,
        good_q=cfg.good_quality, pip=cfg.pip_size, contract=cfg.contract_size)
    return replace(p, **costs)
