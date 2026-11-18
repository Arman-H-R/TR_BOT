# -*- coding: utf-8 -*-
"""
test_rules.py - unit tests for the rules added in v2 (no MT5 needed).

    python -m pytest tests/test_rules.py -q

  * Friday cut-off and the "carried over a weekend" rule
  * the higher-timeframe trend has no look-ahead and the live path (broker H1
    candles) equals the backtest path (H1 resampled from M5)
  * position state: the live bot rebuilds risk / break-even / trail state of an
    open position after a restart, and does not depend on Config.rr
  * the step trail and break-even, live (tick price) vs backtest (candle range)
  * gap fills: a candle opening beyond the stop fills at the open
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import replace
from datetime import time
from types import SimpleNamespace

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import backtest  # noqa: E402
import live_trade  # noqa: E402
import smc_core as core  # noqa: E402


def ts(s):
    return pd.Timestamp(s, tz="America/New_York").tz_convert("UTC")


# --------------------------------------------------------------------------- #
def test_friday_cutoff():
    cfg = replace(core.Config(), friday_close=time(16, 0))
    assert not core.friday_cutoff(ts("2026-11-13 15:55"), cfg)        # Friday before
    assert core.friday_cutoff(ts("2026-11-13 16:00"), cfg)            # Friday at
    assert core.friday_cutoff(ts("2026-11-14 10:00"), cfg)            # Saturday
    assert not core.friday_cutoff(ts("2026-11-16 08:00"), cfg)        # Monday, no entry info
    # a position from Thursday still open on Monday was carried over the weekend
    assert core.friday_cutoff(ts("2026-11-16 08:00"), cfg, entry_ts=ts("2026-11-12 10:00"))
    # Monday -> Thursday of the same week is not
    assert not core.friday_cutoff(ts("2026-11-19 10:00"), cfg, entry_ts=ts("2026-11-16 10:00"))
    off = core.Config()
    off.friday_close = None
    assert not core.friday_cutoff(ts("2026-11-13 16:30"), off)


def _m5(n=3000, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-05 00:00", periods=n, freq="5min", tz="UTC")
    c = 2000 + np.cumsum(rng.normal(0, 1, n))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + rng.random(n)
    l = np.minimum(o, c) - rng.random(n)
    return pd.DataFrame(dict(open=o, high=h, low=l, close=c), index=idx)


def test_trend_live_equals_backtest_and_no_lookahead():
    df = _m5()
    cfg = replace(core.Config(), trend_filter="H1")
    full = core.trend_series(df.index, df.close.to_numpy(), cfg)
    # live: broker H1 candles = the M5 candles resampled, only CLOSED ones
    h1 = df.close.resample("1h", label="left", closed="left").last().dropna()
    for k in (400, 1234, 2999):
        now_open = df.index[k]
        closed = h1[h1.index + pd.Timedelta("1h") <= df.index[-1]]
        live = core.trend_from_htf(closed.index, closed.to_numpy(), df.index[:k + 1], cfg)
        assert live[-1] == full[k]
        # no look-ahead: changing everything after candle k leaves trend[k] alone
        df2 = df.copy()
        df2.iloc[k + 1:, :] = df2.iloc[k + 1:, :] * 1.5
        again = core.trend_series(df2.index, df2.close.to_numpy(), cfg)
        assert again[k] == full[k], now_open


# --------------------------------------------------------------------------- #
def _position(**kw):
    base = dict(ticket=11, type=0, price_open=2000.0, sl=1995.0, tp=2020.0,
                comment="smc r=5.00", time=0, symbol="XAUUSD", volume=0.1, magic=1)
    base.update(kw)
    return SimpleNamespace(**base)


def test_position_state_survives_restart_and_rr_change():
    live_trade.mt5 = SimpleNamespace(POSITION_TYPE_BUY=0)
    cfg = core.Config()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "state.json")
        st = live_trade.PositionState(path)
        st.remember(11, 5.0, 4.0, 2000.0)
        st2 = live_trade.PositionState(path)            # "restart"
        pos = _position(sl=2000.2, tp=2020.0)           # already at break-even
        assert st2.risk(pos, cfg) == 5.0
        cfg.rr = 6.0                                    # TP-based guess would now say 3.33
        assert st2.risk(pos, cfg) == 5.0
        # no file entry -> the order comment
        st3 = live_trade.PositionState(os.path.join(d, "none.json"))
        assert st3.risk(_position(ticket=99, comment="smc r=2.50"), cfg) == 2.5
        # neither -> legacy TP / rr
        assert abs(st3.risk(_position(ticket=98, comment="smc"), cfg) - 20.0 / 6.0) < 1e-9
        st2.forget_except([])
        assert json.load(open(path)) == {}


def test_trade_from_position_reads_be_and_trail():
    cfg = replace(core.Config(), trail_steps=True)
    risk = 5.0
    fresh = live_trade.trade_from_position(1, 2000.0, 1995.0, 2020.0, risk, cfg)
    assert not fresh.be_done and fresh.trail_r == 0
    be = live_trade.trade_from_position(1, 2000.0, 2000.2, 2020.0, risk, cfg)
    assert be.be_done and be.trail_r == 0
    tr = live_trade.trade_from_position(1, 2000.0, 2005.0, 2020.0, risk, cfg)
    assert tr.be_done and abs(tr.trail_r - 1.0) < 1e-9
    # price at +2.5R on a restarted position at break-even -> trail to +1R
    assert core.apply_trailing(be, high=2012.5, low=2012.5, cfg=cfg)
    assert abs(be.sl - 2005.0) < 1e-9
    # and nothing moves again at the same price
    assert not core.apply_trailing(be, high=2012.5, low=2012.5, cfg=cfg)
    # sell side
    s = live_trade.trade_from_position(-1, 2000.0, 2004.0, 1980.0, 4.0, cfg)
    assert not core.apply_breakeven(s, high=1996.0, low=1996.0)   # +1R: not yet (BE at 2R)
    assert core.apply_breakeven(s, high=1992.0, low=1992.0)       # +2R
    assert abs(s.sl - (2000.0 - 0.2)) < 1e-9


def _forced_signal_run(df, cfg, at, sl, monkeypatch, **kw):
    """backtest.run() with the tracker replaced by one that fires a BUY at candle `at`."""
    t_at = df.index[at]

    def fake_update(self, bars, require_session=True, trend=0):
        if bars.time[-1] != t_at:
            return None
        ob = core.OrderBlock(1, len(bars) - 2, sl + 0.4, sl + 0.1, 1)
        bos = core.Bos(1, len(bars) - 3, sl + 1, len(bars) - 3)
        lq = core.Liquidity(sl + 0.5, 2, len(bars) - 4, "pullback")
        return core.Signal(time=t_at, direction=1, touch_price=sl + 0.4, sl=sl, ob=ob, bos=bos,
                           lq=lq, impulse_bars=1, pullback_bars=5, ob_time=df.index[at - 1])
    monkeypatch.setattr(core.SetupTracker, "update", fake_update)
    trades, _, _ = backtest.run(df, cfg, spread=0.0, balance=10000, require_session=False, **kw)
    return trades


def test_gap_fill_in_backtest(monkeypatch):
    """A long whose stop is jumped over by the next candle's open exits at that open."""
    n = 400
    idx = pd.date_range("2026-01-05 13:00", periods=n, freq="5min", tz="UTC")
    c = np.full(n, 100.0)
    df = pd.DataFrame(dict(open=c, high=c + 0.05, low=c - 0.05, close=c), index=idx)
    at = 300
    df.iloc[at + 2] = [97.0, 97.1, 96.9, 97.0]           # gaps 2.0 below a 98.0 stop
    cfg = core.Config()
    cfg.min_sl_pips = 1.0
    gap = _forced_signal_run(df, cfg, at, 98.0, monkeypatch)
    assert len(gap) == 1 and gap[0].exit == 97.0 and abs(gap[0].r + 1.5) < 1e-9
    old = _forced_signal_run(df, cfg, at, 98.0, monkeypatch, gap_fill=False)
    assert old[0].exit == 98.0 and abs(old[0].r + 1.0) < 1e-9


def test_friday_close_in_backtest(monkeypatch):
    """An open position is flattened at the Friday cut-off, not carried over."""
    idx = pd.date_range("2026-11-12 22:00", periods=400, freq="5min", tz="UTC")   # Thu 17:00 NY
    c = np.full(len(idx), 100.0)
    df = pd.DataFrame(dict(open=c, high=c + 0.05, low=c - 0.05, close=c), index=idx)
    cfg = core.Config()
    cfg.min_sl_pips = 1.0
    cfg.friday_close = time(16, 0)
    tr = _forced_signal_run(df, cfg, 220, 98.0, monkeypatch)                    # Fri 11:20 NY
    assert len(tr) == 1 and tr[0].outcome == "FRIDAY"
    assert tr[0].exit_time == ts("2026-11-13 16:00")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_server_offset_needs_a_live_tick(monkeypatch):
    """A frozen (stale) quote must not set the server clock; a moving one does."""
    import time as _t
    now = _t.time()
    monkeypatch.setattr(live_trade._time, "sleep", lambda s: None)
    # frozen quote, one hour old -> falls back (the 2026-11-18 start-up bug gave UTC-1)
    frozen = SimpleNamespace(time=int(now - 3600), time_msc=1)
    live_trade.mt5 = SimpleNamespace(symbol_info_tick=lambda s: frozen)
    assert live_trade.server_utc_offset("XAUUSD", fallback=0.0, wait=0.01) == 0.0
    # moving quote on a UTC+2 server
    seq = iter(range(10 ** 6))
    live_trade.mt5 = SimpleNamespace(symbol_info_tick=lambda s: SimpleNamespace(
        time=int(_t.time() + 7200), time_msc=next(seq)))
    assert live_trade.server_utc_offset("XAUUSD", wait=5) == 2.0
