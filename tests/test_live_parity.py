# -*- coding: utf-8 -*-
"""
test_live_parity.py - does live_trade.py take the same trades as backtest.py?

Replays real candles through the live bot's candle handling (no orders, no
account needed beyond reading history) and compares the entries with
backtest.run() on the very same candles.

    python tests/test_live_parity.py                    # 20 000 M5 candles from MT5
    python tests/test_live_parity.py --bars 60000
    python tests/test_live_parity.py --csv data/XAUUSD_M5.csv

Four runs:
  backtest   backtest.run() - the reference
  old live   the loop as it was: tracker.update() only when flat, inside the
             session and under the daily ceiling
  new live   the fixed loop: live_trade.feed_tracker() on every candle
  new live, stalls + restarts
             same, but the bot misses random polls (catch-up path) and is
             restarted with an empty tracker now and then (warm-up path)

The live runs use the backtest's own exits to know when a position is open, so
they can only agree with it if they pick exactly the same entries.
"""

from __future__ import annotations

import argparse
import os
import random
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import backtest  # noqa: E402
import live_trade  # noqa: E402
import smc_core as core  # noqa: E402


def simulate_live(bars: core.Bars, cfg: core.Config, spread: float, spec: dict,
                  exits: dict, mode: str, seed: int = 1):
    """Entry times the live loop would take. mode: old | new | chaos."""
    need = max(cfg.required_bars(), 200)
    rng = random.Random(seed)
    tracker = core.SetupTracker(cfg)
    last_seen = None
    open_until = None                  # exit time of the position we hold
    taken, per_day = [], {}
    traded_obs = set()                 # the journal, survives restarts
    stalls = restarts = 0
    seen = set()                       # every signal the tracker produced
    down = set()                       # candles the bot was not polling
    stops_level = spec.get("stops_level", 0) * spec.get("point", 0.01)
    trend_all = core.trend_series(bars.time, bars.close, cfg)   # what the live bot reads from H1/H4

    for i in range(2 * need, len(bars)):
        now = pd.Timestamp(bars.time[i])
        if open_until is not None and now >= open_until:
            open_until = None          # closed during this candle (SL/TP/BE)
        position = open_until is not None

        if mode == "chaos":
            if rng.random() < 0.002:   # bot restarted: empty memory
                tracker, last_seen = core.SetupTracker(cfg), None
                restarts += 1
            if rng.random() < 0.03:    # this poll never happened
                stalls += 1
                down.add(now)
                continue

        day = core.DayLimit(cfg)._day(now)
        today = per_day.get(day, 0)

        if mode == "old":
            window = bars.window(i - need + 1, i + 1)
            signal = None
            if (not position and core.in_session(now, cfg)
                    and today < cfg.max_trades_per_day + cfg.extra_trades_if_quality):
                signal = tracker.update(window)
            if signal is not None:
                seen.add((pd.Timestamp(signal.time), str(signal.ob_time)))
        else:
            history = bars.window(i - 2 * need + 1, i + 1)
            signal, _, stale = live_trade.feed_tracker(
                tracker, history, need, last_seen, trend_all[i - 2 * need + 1:i + 1])
            last_seen = now
            for s in stale + ([signal] if signal is not None else []):
                seen.add((pd.Timestamp(s.time), str(s.ob_time)))
            if position or not core.in_session(now, cfg) or core.friday_cutoff(now, cfg):
                signal = None

        if signal is None:
            continue
        cap = cfg.max_trades_per_day
        if signal.quality >= cfg.good_quality:
            cap += cfg.extra_trades_if_quality
        if today >= cap or str(signal.ob_time) in traded_obs:
            continue
        # take_signal(): same fill and the same rails as the backtest
        fill = bars.close[i] + spread if signal.direction > 0 else bars.close[i]
        trade = core.build_trade(signal, fill, cfg)
        if trade is None:
            continue
        if stops_level and (abs(fill - trade.sl) < stops_level
                            or abs(trade.tp - fill) < stops_level):
            continue
        taken.append(now)
        traded_obs.add(str(signal.ob_time))
        per_day[day] = today + 1
        # unknown exit = the backtest never took it; hold it one candle
        open_until = exits.get(now, pd.Timestamp(bars.time[min(i + 1, len(bars) - 1)]))
    return dict(taken=taken, seen=seen, stalls=stalls, restarts=restarts, down=down)


def compare(name: str, got, ref) -> bool:
    key = lambda x: x[0] if isinstance(x, tuple) else x
    got_s, ref_s = set(got), set(ref)
    extra, missing = sorted(got_s - ref_s), sorted(ref_s - got_s)
    ok = not extra and not missing
    print(f"{name:<34}{len(got):>7}{len(got_s & ref_s):>9}{len(extra):>7}{len(missing):>9}"
          f"   {'PASS' if ok else 'DIFFERS'}")
    for t in (extra[:3] + missing[:3]):
        print(f"{'':<34}{'extra  ' if t in extra else 'missing'} {key(t):%Y-%m-%d %H:%M}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=20000)
    ap.add_argument("--csv")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    if args.csv:
        df, spec = backtest.load_csv(args.csv)
        df = df.iloc[-args.bars:]
    else:
        df, spec = backtest.load_mt5(args.symbol, "M5", args.bars)
    spread = spec.get("spread", 0.30)
    cfg = core.Config(symbol=args.symbol)        # the shipped defaults
    need = max(cfg.required_bars(), 200)
    # the live runs need 2 x need candles of history before their first decision;
    # give the backtest the same start so both look at the same candles
    df = df.copy()
    bars = core.Bars.from_dataframe(df)
    print(f"{args.symbol} M5 {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d}, "
          f"{len(df)} candles, spread {spread:.2f}\n")

    trades, _, _ = backtest.run(df, cfg, spread, balance=4700.0, spec=spec)
    first_live = pd.Timestamp(bars.time[2 * need])
    ref = [pd.Timestamp(t.entry_time) for t in trades if pd.Timestamp(t.entry_time) >= first_live]
    exits = {pd.Timestamp(t.entry_time): pd.Timestamp(t.exit_time) for t in trades}

    # the tracker's own signal stream, fed every candle exactly like the backtest
    ref_tracker, ref_seen = core.SetupTracker(cfg), set()
    for i in range(need, len(bars)):
        sig = ref_tracker.update(bars.window(i - need + 1, i + 1))
        if sig is not None and pd.Timestamp(sig.time) >= first_live:
            ref_seen.add((pd.Timestamp(sig.time), str(sig.ob_time)))

    runs = {m: simulate_live(bars, cfg, spread, spec, exits, m, args.seed)
            for m in ("old", "new", "chaos")}
    chaos = runs["chaos"]
    label = {"old": "old live loop", "new": "new live loop",
             "chaos": f"new + {chaos['stalls']} stalls, {chaos['restarts']} restarts"}

    def live_part(seen):
        return {x for x in seen if x[0] >= first_live}

    print("1) setups the tracker signals (any position / session / cap state)")
    print(f"{'run':<34}{'count':>7}{'same':>9}{'extra':>7}{'missing':>9}")
    print(f"{'backtest tracker (reference)':<34}{len(ref_seen):>7}")
    ok = {}
    for m in ("old", "new", "chaos"):
        ok["sig_" + m] = compare(label[m], sorted(live_part(runs[m]["seen"])), sorted(ref_seen))

    print()
    print("2) trades taken")
    print(f"{'run':<34}{'count':>7}{'same':>9}{'extra':>7}{'missing':>9}")
    print(f"{'backtest.run() (reference)':<34}{len(ref):>7}")
    for m in ("old", "new"):
        ok["trd_" + m] = compare(label[m], runs[m]["taken"], ref)
    got = set(chaos["taken"])
    missing = set(ref) - got
    excused = {t for t in missing if t in chaos["down"]}
    compare(label["chaos"], chaos["taken"], ref)
    print(f"{'':<34}{len(excused)} of {len(missing)} missing fell on a candle the bot "
          f"was down for (cannot be traded late);")
    print(f"{'':<34}the rest follow from those (a different position was open)")

    passed = ok["sig_new"] and ok["sig_chaos"] and ok["trd_new"]
    print()
    print("RESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
