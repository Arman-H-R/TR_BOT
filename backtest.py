# -*- coding: utf-8 -*-
"""
backtest.py - run smc_core.py over history, the same way live_trade.py runs it live.

    python backtest.py --mt5 --symbol XAUUSD --balance 4700 --out reports/smc_m5
    python backtest.py --csv data/XAUUSD_M5.csv --start 2026-01-01 --risk 0.25
    python backtest.py --mt5 --compare           # a few rule variations side by side
    python backtest.py --mt5 --list              # print every trade

Parity with live_trade.py - the whole point of the three-file layout:

  * every closed candle goes to `core.SetupTracker.update()`, which watches each
    setup from the break until price touches the order block and decides AT THE
    TOUCH: valid -> trade, not valid -> the setup fails and is never retried
    (Config.fail_on_touch). live_trade.py drives the same tracker.
  * a signal is taken as a MARKET order on the candle that produced it, because
    that is what live_trade.py does (`fill = tick.ask if buy else tick.bid`), so
    the backtest fills at that candle's close plus the spread for a buy
  * the trade is then built with `core.build_trade(signal, fill, cfg)`, so R and
    the 3R target come from the REAL fill while the stop comes from the order block
  * `core.apply_breakeven()` moves the stop to entry + cost at +1R
  * `core.lot_size()` sizes the position, `core.DayLimit` caps trades per day

Where the simulation has to guess, it guesses against the trade:

  * management starts on the candle AFTER the entry (the signal candle's own
    range already happened while the order was being placed)
  * if one candle contains both the stop and the target, the STOP is taken
  * a candle that OPENS beyond the stop (weekend / daily-break / news gap) fills
    the stop at that open, not at the stop price - a stop order cannot do better
    (gap_fill=False restores the old, optimistic behaviour)
  * sell exits are compared against high+spread / low+spread, i.e. the ask side
  * spread and commission are charged on every trade
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass, replace
from datetime import time as dtime
from typing import Optional

import numpy as np
import pandas as pd

import smc_core as core

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def load_mt5(symbol: str, timeframe: str, bars: int) -> tuple[pd.DataFrame, dict]:
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"MT5 did not start: {mt5.last_error()} - open the terminal and log in")
    try:
        if not mt5.symbol_select(symbol, True):
            raise SystemExit(f"{symbol} is not available on this account")
        tf = getattr(mt5, f"TIMEFRAME_{timeframe.upper()}")
        rates = mt5.copy_rates_from_pos(symbol, tf, 1, min(bars, 99000))   # 1 = closed candles
        if rates is None or len(rates) == 0:
            raise SystemExit(f"no {timeframe} history: {mt5.last_error()}")
        info = mt5.symbol_info(symbol)
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")
        spread = (float(df["spread"].median()) * info.point if "spread" in df
                  else info.spread * info.point)
        spec = dict(spread=spread, point=info.point, digits=info.digits,
                    contract_size=info.trade_contract_size, volume_min=info.volume_min,
                    volume_max=min(info.volume_max, 100.0), volume_step=info.volume_step,
                    stops_level=info.trade_stops_level)
        return df[["open", "high", "low", "close"]], spec
    finally:
        mt5.shutdown()


def load_csv(path: str) -> tuple[pd.DataFrame, dict]:
    if not os.path.exists(path):
        raise SystemExit(f"file not found: {path}")
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next((c for c in ("time", "datetime", "date", "timestamp") if c in df.columns), None)
    if tcol is None:
        raise SystemExit(f"{path}: no time column")
    df["time"] = pd.to_datetime(df[tcol], utc=True)
    df = df.set_index("time").sort_index()
    return df[["open", "high", "low", "close"]].astype(float), {}


# --------------------------------------------------------------------------- #
# the simulation
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    direction: int
    side: str
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry: float
    sl: float
    tp: float
    exit: float
    volume: float
    risk_pips: float
    risk_money: float
    pnl: float
    r: float
    outcome: str            # TP | SL | BE | TRAIL | SESSION | FRIDAY | END
    bars_held: int
    be_done: bool
    ambiguous: bool
    impulse_bars: int
    pullback_bars: int
    lq_level: float
    lq_touches: int
    lq_where: str
    ob_top: float
    ob_bottom: float
    bos_level: float
    equity_after: float
    quality: int = 0
    quality_tags: str = ""


def run(df: pd.DataFrame, cfg: core.Config, spread: float, commission: float = 0.0,
        balance: float = 10000.0, max_open: int = 1, max_spread_pips: float = 10.0,
        require_session: bool = True, spec: Optional[dict] = None,
        progress: bool = False, gap_fill: bool = True) -> tuple[list[Result], list[dict], dict]:
    spec = spec or {}
    bars = core.Bars.from_dataframe(df)
    n = len(bars)
    need = max(cfg.required_bars(), 200)
    if n <= need + 10:
        raise SystemExit(f"only {n} candles - need more than {need}")

    trend = core.trend_series(bars.time, bars.close, cfg)   # 0s when the filter is off
    day_limit = core.DayLimit(cfg)
    tracker = core.SetupTracker(cfg)
    counters: dict[str, int] = {}
    def tick(key: str) -> None:
        counters[key] = counters.get(key, 0) + 1

    equity = balance
    open_trades: list[dict] = []
    done: list[Result] = []
    curve: list[dict] = []
    used_obs: set = set()
    vol_min = spec.get("volume_min", 0.01)
    vol_max = spec.get("volume_max", 100.0)
    vol_step = spec.get("volume_step", 0.01)

    for i in range(need, n):
        now = bars.time[i]
        high, low, close = bars.high[i], bars.low[i], bars.close[i]
        friday = core.friday_cutoff(now, cfg)

        # ---- 1. manage what is already open, on this candle ---------------
        still: list[dict] = []
        for t in open_trades:
            trade: core.Trade = t["trade"]
            d = trade.direction
            if i <= t["entry_index"]:          # no management on the entry candle
                still.append(t)
                continue

            # the ask side is what closes a short
            hi = high + (spread if d < 0 else 0.0)
            lo = low + (spread if d < 0 else 0.0)
            hit_sl = lo <= trade.sl if d > 0 else hi >= trade.sl
            hit_tp = hi >= trade.tp if d > 0 else lo <= trade.tp

            stop_kind = ("TRAIL" if trade.trail_r > 0 else "BE" if trade.be_done else "SL")
            op = bars.open[i] + (spread if d < 0 else 0.0)
            stop_px = trade.sl
            if gap_fill and ((op < trade.sl) if d > 0 else (op > trade.sl)):
                stop_px = op                   # gapped through the stop
            if hit_sl and hit_tp:
                price, why, ambiguous = stop_px, stop_kind, True
            elif hit_sl:
                price, why, ambiguous = stop_px, stop_kind, False
            elif hit_tp:
                price, why, ambiguous = trade.tp, "TP", False
            else:
                core.apply_breakeven(trade, high=hi, low=lo)      # rule 6: +1R -> entry + tax
                core.apply_trailing(trade, high=hi, low=lo, cfg=cfg)  # optional step trail
                if core.friday_cutoff(now, cfg, entry_ts=t["entry_time"]):
                    price, why, ambiguous = (close + spread if d < 0 else close), "FRIDAY", False
                elif (cfg.close_at_session_end and require_session
                        and not core.in_session(now, cfg)):
                    price, why, ambiguous = (close + spread if d < 0 else close), "SESSION", False
                else:
                    t["bars"] += 1
                    still.append(t)
                    continue

            pnl = (price - trade.entry) * d * t["volume"] * cfg.contract_size \
                - commission * t["volume"]
            equity += pnl
            tick(f"exit {why}")
            s: core.Signal = t["signal"]
            done.append(Result(
                direction=d, side=s.side, signal_time=s.time, entry_time=t["entry_time"],
                exit_time=now, entry=trade.entry, sl=trade.initial_sl, tp=trade.tp,
                exit=price, volume=t["volume"], risk_pips=cfg.to_pips(trade.risk),
                risk_money=t["risk_money"], pnl=pnl,
                r=pnl / t["risk_money"] if t["risk_money"] else 0.0,
                outcome=why, bars_held=t["bars"], be_done=trade.be_done, ambiguous=ambiguous,
                impulse_bars=s.impulse_bars, pullback_bars=s.pullback_bars,
                lq_level=s.lq.level, lq_touches=s.lq.touches, lq_where=s.lq.where,
                ob_top=s.ob.top, ob_bottom=s.ob.bottom, bos_level=s.bos.level,
                equity_after=equity, quality=getattr(s, "quality", 0),
                quality_tags="+".join(getattr(s, "quality_parts", {}) or {})))
        open_trades = still

        # ---- 2. ask the core for a setup on this closed candle ------------
        window = bars.window(i - need + 1, i + 1)
        signal = tracker.update(window, require_session=require_session, trend=int(trend[i]))
        curve.append(dict(time=now, equity=equity, open=len(open_trades)))

        if signal is None:
            continue
        tick("signals from the core")

        key = signal.ob_time if signal.ob_time is not None else (signal.ob.top, signal.ob.bottom)
        if key in used_obs:
            tick("skipped: this order block was already traded")
            continue
        if len(open_trades) >= max_open:
            tick("skipped: a trade is already open")
            continue
        if friday:
            tick("skipped: Friday close")
            continue
        if not day_limit.allows(now, getattr(signal, "quality", 0)):
            tick("skipped: daily trade limit")
            continue
        if cfg.to_pips(spread) > max_spread_pips:
            tick("skipped: spread too wide")
            continue

        fill = close + spread if signal.direction > 0 else close    # market, like live
        trade = core.build_trade(signal, fill, cfg)
        if trade is None:
            tick("skipped: stop outside the min/max pips rails")
            continue
        stops_level = spec.get("stops_level", 0) * spec.get("point", 0.01)
        if stops_level and (abs(fill - trade.sl) < stops_level or abs(trade.tp - fill) < stops_level):
            tick("skipped: broker stop distance")
            continue

        volume = core.lot_size(equity, trade.risk, cfg, vol_min, vol_max, vol_step)
        risk_money = trade.risk * volume * cfg.contract_size
        if risk_money <= 0:
            tick("skipped: zero risk")
            continue
        used_obs.add(key)
        day_limit.register(now)
        open_trades.append(dict(trade=trade, signal=signal, volume=volume,
                                risk_money=risk_money, entry_index=i, entry_time=now, bars=0))
        tick("TRADES TAKEN")
        if progress and len(done) and len(done) % 25 == 0:
            print(f"  ... {now:%Y-%m-%d}: {len(done)} closed, equity {equity:,.0f}", flush=True)

    # anything still open at the end
    for t in open_trades:
        trade, s = t["trade"], t["signal"]
        d = trade.direction
        price = bars.close[-1] + (spread if d < 0 else 0.0)
        pnl = (price - trade.entry) * d * t["volume"] * cfg.contract_size - commission * t["volume"]
        equity += pnl
        done.append(Result(
            direction=d, side=s.side, signal_time=s.time, entry_time=t["entry_time"],
            exit_time=bars.time[-1], entry=trade.entry, sl=trade.initial_sl, tp=trade.tp,
            exit=price, volume=t["volume"], risk_pips=cfg.to_pips(trade.risk),
            risk_money=t["risk_money"], pnl=pnl,
            r=pnl / t["risk_money"] if t["risk_money"] else 0.0, outcome="END",
            bars_held=t["bars"], be_done=trade.be_done, ambiguous=False,
            impulse_bars=s.impulse_bars, pullback_bars=s.pullback_bars, lq_level=s.lq.level,
            lq_touches=s.lq.touches, lq_where=s.lq.where, ob_top=s.ob.top,
            ob_bottom=s.ob.bottom, bos_level=s.bos.level, equity_after=equity,
            quality=getattr(s, "quality", 0),
            quality_tags="+".join(getattr(s, "quality_parts", {}) or {})))
        tick("exit END")
    for key, value in tracker.counters.items():
        counters[key] = counters.get(key, 0) + value
    counters["_failures"] = tracker.failures
    return done, curve, counters


# --------------------------------------------------------------------------- #
# numbers
# --------------------------------------------------------------------------- #
def summarise(trades: list[Result], df: pd.DataFrame, balance: float) -> dict:
    weeks = max((df.index[-1] - df.index[0]).days / 7.0, 1e-9)
    months = max((df.index[-1] - df.index[0]).days / 30.44, 1e-9)
    out = dict(n=0, per_week=0.0, per_month=0.0, win=0.0, avg_r=0.0, total_r=0.0, pf=0.0,
               dd_r=0.0, end=balance, ret=0.0, weeks=weeks, months=months, expectancy=0.0,
               avg_win=0.0, avg_loss=0.0, max_dd_money=0.0)
    if not trades:
        return out
    r = np.array([t.r for t in trades], float)
    wins, losses = r[r > 0.01], r[r < -0.01]
    cum = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate(([0.0], cum)))[1:]
    eq = np.array([t.equity_after for t in trades], float)
    eq_peak = np.maximum.accumulate(np.concatenate(([balance], eq)))[1:]
    out.update(n=len(r), per_week=len(r) / weeks, per_month=len(r) / months,
               win=100.0 * len(wins) / len(r), avg_r=float(r.mean()), total_r=float(r.sum()),
               pf=float(wins.sum() / -losses.sum()) if len(losses) else math.inf,
               dd_r=float((peak - cum).max()), end=float(eq[-1]),
               ret=100.0 * (float(eq[-1]) - balance) / balance,
               avg_win=float(wins.mean()) if len(wins) else 0.0,
               avg_loss=float(losses.mean()) if len(losses) else 0.0,
               max_dd_money=float((eq_peak - eq).max()))
    return out


def report(trades, counters, df, cfg, s, spread, balance, show_list=False) -> None:
    line = "=" * 78
    print(line)
    print(f"{cfg.symbol} {cfg.timeframe} | {df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d} "
          f"| {s['weeks']:.0f} weeks | {len(df):,} candles")
    print(f"spread {spread:.2f} ({cfg.to_pips(spread):.1f} pips) | risk {cfg.risk_percent}%"
          f" | TP {cfg.rr:.0f}R | SL = OB +{cfg.sl_buffer_pips:.0f} pips "
          f"| BE at {cfg.be_trigger_r:.0f}R +{cfg.be_cost_pips:.0f} pips")
    print(f"session {cfg.session_start:%H:%M}-{cfg.session_end:%H:%M} {cfg.session_tz} "
          f"| max {cfg.max_trades_per_day}/day | retouch entry {cfg.entry_on_retouch} "
          f"| LQ {cfg.lq_tolerance_pips:.0f} pips x{cfg.lq_min_touches}"
          f"{' (pre-OB allowed)' if cfg.lq_allow_pre_ob else ''}")
    print(line)

    print("\nWHAT HAPPENED TO THE SETUPS")
    failures = counters.get("_failures") or []
    for key, value in sorted(((k, v) for k, v in counters.items()
                              if not k.startswith("_")), key=lambda kv: -kv[1]):
        print(f"  {value:>6}  {key}")
    if failures:
        reasons: dict[str, int] = {}
        for _, _, why in failures:
            reasons[why.split(":")[0]] = reasons.get(why.split(":")[0], 0) + 1
        print("\n  why setups failed before or at the touch:")
        for why, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {count:>6}  {why}")

    print("\nRESULT")
    print(f"  trades          {s['n']}  ->  {s['per_week']:.1f} per week, {s['per_month']:.1f} per month")
    print(f"  win rate        {s['win']:.1f}%   (average win {s['avg_win']:+.2f} R, "
          f"average loss {s['avg_loss']:+.2f} R)")
    print(f"  expectancy      {s['avg_r']:+.3f} R per trade     total {s['total_r']:+.1f} R")
    print(f"  profit factor   {s['pf']:.2f}")
    print(f"  equity          {balance:,.2f} -> {s['end']:,.2f}   ({s['ret']:+.1f}%)")
    print(f"  worst run       {s['dd_r']:.1f} R   (money drawdown {s['max_dd_money']:,.2f})")
    if trades:
        kinds: dict[str, int] = {}
        for t in trades:
            kinds[t.outcome] = kinds.get(t.outcome, 0) + 1
        print(f"  exits           " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
        be = sum(1 for t in trades if t.be_done)
        amb = sum(1 for t in trades if t.ambiguous)
        print(f"  break-even      {be} trade(s) had the stop moved to entry + cost")
        if amb:
            print(f"  ambiguous       {amb} candle(s) held both the stop and the target "
                  f"- counted as the stop")

    if trades:
        months: dict[str, list] = {}
        for t in trades:
            months.setdefault(pd.Timestamp(t.exit_time).strftime("%Y-%m"), []).append(t.r)
        print("\nBY MONTH")
        for key in sorted(months):
            rs = months[key]
            bar = "#" * min(28, int(abs(sum(rs)) * 2))
            print(f"  {key}  {len(rs):>3} trades  {sum(rs):+6.1f} R  "
                  f"{'+' if sum(rs) >= 0 else '-'}{bar}")

        by_q: dict = {}
        for t in trades:
            by_q.setdefault(t.quality, []).append(t.r)
        if len(by_q) > 1:
            print("\nBY ORDER BLOCK QUALITY (ob_quality in smc_core.py)")
            for q in sorted(by_q):
                rs = by_q[q]
                print(f"  quality {q:<4}{len(rs):>5} trades  avg {np.mean(rs):+.3f} R  "
                      f"total {np.sum(rs):+6.1f} R")

        print("\nBY SIDE / LQ POSITION")
        for label, pick in (("BUY", lambda t: t.direction > 0), ("SELL", lambda t: t.direction < 0),
                            ("LQ above/below OB", lambda t: t.lq_where != "pre_ob"),
                            ("BE armed", lambda t: t.be_done),
                            ("no BE", lambda t: not t.be_done)):
            group = [t.r for t in trades if pick(t)]
            if group:
                print(f"  {label:<20}{len(group):>4} trades  avg {np.mean(group):+.3f} R  "
                      f"total {np.sum(group):+6.1f} R")

    if show_list and trades:
        print("\nEVERY TRADE")
        print(f"  {'entry time':<17}{'side':<5}{'entry':>9}{'sl':>9}{'tp':>9}{'lots':>6}"
              f"{'R':>7}  outcome")
        for t in trades:
            print(f"  {t.entry_time:%Y-%m-%d %H:%M} {t.side:<5}{t.entry:>9.2f}{t.sl:>9.2f}"
                  f"{t.tp:>9.2f}{t.volume:>6.2f}{t.r:>+7.2f}  {t.outcome}")

    print("\nHOW MUCH TO TRUST THIS")
    if s["n"] < 30:
        print(f"  * {s['n']} trades is too few to prove anything")
    else:
        se = 1.6 / math.sqrt(s["n"])
        verdict = "inside" if abs(s["avg_r"]) < 2 * se else "outside"
        print(f"  * {s['n']} trades: the average carries an error of about +/-{se:.2f} R, "
              f"so {s['avg_r']:+.3f} is {verdict} two standard errors of zero")
    print(f"  * entries are market fills at the candle close plus spread, as live_trade.py does")
    print(f"  * a candle holding both stop and target counts as a loss")
    print(f"  * forward test on a demo account before risking money")
    print(line)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def build_cfg(args) -> core.Config:
    cfg = core.Config()
    if args.symbol:
        cfg.symbol = args.symbol
    if args.risk is not None:
        cfg.risk_percent = args.risk
    if args.rr is not None:
        cfg.rr = args.rr
    for item in (args.set or []):
        key, value = item.split("=", 1)
        if not hasattr(cfg, key):
            raise SystemExit(f"unknown config field {key!r}")
        current = getattr(cfg, key)
        if key == "friday_close" or isinstance(current, dtime):
            v = value.strip().lower()
            setattr(cfg, key, None if v in ("none", "off", "") else
                    dtime(*(int(x) for x in v.split(":"))))
        elif isinstance(current, bool):
            setattr(cfg, key, value.strip().lower() in ("1", "true", "yes"))
        elif isinstance(current, int) and not isinstance(current, bool):
            setattr(cfg, key, int(value))
        elif isinstance(current, float):
            setattr(cfg, key, float(value))
        else:
            setattr(cfg, key, value)
    return cfg


VARIANTS = [
    ("as configured", {}),
    ("TP 2R (your first spec)", {"rr": 2.0}),
    ("TP 4R", {"rr": 4.0}),
    ("no break-even move", {"be_trigger_r": 99.0}),
    ("first touch only", {"entry_on_retouch": False}),
    ("LQ 5 pips", {"lq_tolerance_pips": 5.0}),
    ("LQ pre-OB allowed", {"lq_allow_pre_ob": True}),
    ("pullback impulse+4", {"pullback_extra_bars": 4}),
    ("swing 3", {"swing_lookback": 3}),
    ("all day (no session)", {"__no_session": True}),
    ("6 trades a day", {"max_trades_per_day": 6}),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest smc_core.py")
    ap.add_argument("--mt5", action="store_true", help="read history from MetaTrader 5")
    ap.add_argument("--csv", help="read an M5 csv instead")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--timeframe", default="M5")
    ap.add_argument("--bars", type=int, default=99000)
    ap.add_argument("--start", help="only candles from this date")
    ap.add_argument("--end", help="only candles before this date")
    ap.add_argument("--balance", type=float, default=10000.0)
    ap.add_argument("--risk", type=float, help="percent risked per trade")
    ap.add_argument("--rr", type=float, help="target in R (3 = 3R)")
    ap.add_argument("--spread", type=float, help="price units; default = the MT5 median")
    ap.add_argument("--commission", type=float, default=0.0, help="per lot, round turn")
    ap.add_argument("--max-open", type=int, default=1)
    ap.add_argument("--max-spread-pips", type=float, default=10.0)
    ap.add_argument("--no-session", action="store_true", help="ignore the session filter")
    ap.add_argument("--set", action="append", metavar="field=value",
                    help="override any Config field, e.g. --set rr=2 --set swing_lookback=3")
    ap.add_argument("--out", help="write <out>_trades.csv and <out>_equity.csv")
    ap.add_argument("--list", action="store_true", help="print every trade")
    ap.add_argument("--compare", action="store_true", help="run rule variations side by side")
    args = ap.parse_args()

    if args.csv:
        df, spec = load_csv(args.csv)
    else:
        df, spec = load_mt5(args.symbol, args.timeframe, args.bars)
    if args.start:
        df = df[df.index >= pd.Timestamp(args.start, tz="UTC")]
    if args.end:
        df = df[df.index < pd.Timestamp(args.end, tz="UTC")]
    spread = args.spread if args.spread is not None else spec.get("spread", 0.30)
    cfg = build_cfg(args)

    if args.compare:
        print(f"{cfg.symbol} {cfg.timeframe} {df.index[0]:%Y-%m-%d}..{df.index[-1]:%Y-%m-%d} "
              f"| spread {spread:.2f} | same candles for every row\n")
        print(f"{'variant':<26}{'trades':>7}{'/week':>7}{'win':>6}{'avg R':>8}{'total R':>9}"
              f"{'PF':>6}{'worst':>8}{'return':>9}")
        for label, changes in VARIANTS:
            variant = replace(cfg)
            no_session = changes.pop("__no_session", False) if changes else False
            for key, value in changes.items():
                setattr(variant, key, value)
            trades, _, _ = run(df, variant, spread, args.commission, args.balance,
                               args.max_open, args.max_spread_pips,
                               require_session=not (args.no_session or no_session), spec=spec)
            s = summarise(trades, df, args.balance)
            print(f"{label:<26}{s['n']:>7}{s['per_week']:>7.1f}{s['win']:>5.0f}%"
                  f"{s['avg_r']:>+8.3f}{s['total_r']:>+9.1f}{s['pf']:>6.2f}"
                  f"{s['dd_r']:>7.1f}R{s['ret']:>+8.1f}%")
        print("\nMore trades is not the same as more profit - read the avg R column.")
        return 0

    trades, curve, counters = run(df, cfg, spread, args.commission, args.balance,
                                  args.max_open, args.max_spread_pips,
                                  require_session=not args.no_session, spec=spec, progress=True)
    s = summarise(trades, df, args.balance)
    report(trades, counters, df, cfg, s, spread, args.balance, show_list=args.list)

    out = args.out or "trades"
    if trades:
        folder = os.path.dirname(out)
        if folder:
            os.makedirs(folder, exist_ok=True)
        path = f"{out}_trades.csv" if args.out else "trades.csv"
        pd.DataFrame([t.__dict__ for t in trades]).to_csv(path, index=False)
        print(f"\ntrades written to {path}")
        if args.out:
            pd.DataFrame(curve).to_csv(f"{out}_equity.csv", index=False)
            print(f"equity curve written to {out}_equity.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
