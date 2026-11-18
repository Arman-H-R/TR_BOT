# -*- coding: utf-8 -*-
"""
monthly_report.py - the per-month table: profit %, buys/sells, trades per week,
full TP / SL / break-even counts, plus win rate, R, profit factor, drawdown.

    python monthly_report.py                       # v2 backtest, last 12 months (data/XAUUSD_M5.csv)
    python monthly_report.py --months 17
    python monthly_report.py --forward             # the demo forward test (reports/forward_trades.csv)
    python monthly_report.py --forward --since 2026-11-19

Writes reports/monthly_<backtest|forward>.csv as well.
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

import backtest
import smc_core as core

HERE = os.path.dirname(os.path.abspath(__file__))


def table(t: pd.DataFrame, balance: float) -> pd.DataFrame:
    t = t.sort_values("entry_time").copy()
    t["month"] = t.entry_time.dt.strftime("%Y-%m")
    rows, bal = [], balance
    for month, g in t.groupby("month"):
        start = bal
        eq = start + g.pnl.cumsum().to_numpy()
        peak = np.maximum.accumulate(np.r_[start, eq])[1:]
        days = (g.entry_time.max().normalize() - g.entry_time.min().normalize()).days
        first = pd.Timestamp(month + "-01", tz="UTC")
        span = min(pd.Timestamp(g.entry_time.max()), first + pd.offsets.MonthEnd(1)) - first
        weeks = max(span.days + 1, 7) / 7.0
        gains, losses = g.pnl[g.pnl > 0].sum(), -g.pnl[g.pnl < 0].sum()
        bal = float(eq[-1])
        rows.append({
            "month": month,
            "profit %": round((bal - start) / start * 100, 2),
            "trades": len(g),
            "buy": int((g.side == "BUY").sum()),
            "sell": int((g.side == "SELL").sum()),
            "trades/week": round(len(g) / weeks, 1),
            "TP full": int((g.outcome == "TP").sum()),
            "SL": int((g.outcome == "SL").sum()),
            "BE": int((g.outcome == "BE").sum()),
            "Friday/other": int((~g.outcome.isin(["TP", "SL", "BE"])).sum()),
            "win %": round((g.pnl > 0).mean() * 100, 1),
            "R": round(g.r.sum(), 1),
            "PF": round(gains / losses, 2) if losses else float("inf"),
            "max DD %": round(((peak - eq) / peak).max() * 100, 1),
            "balance": round(bal, 2),
        })
    return pd.DataFrame(rows)


def totals(t: pd.DataFrame, balance: float, weeks: float) -> dict:
    eq = balance + t.pnl.cumsum().to_numpy()
    peak = np.maximum.accumulate(np.r_[balance, eq])[1:]
    gains, losses = t.pnl[t.pnl > 0].sum(), -t.pnl[t.pnl < 0].sum()
    streak = best = 0
    for p in t.pnl:
        streak = streak + 1 if p < 0 else 0
        best = max(best, streak)
    return {"month": "TOTAL", "profit %": round((eq[-1] - balance) / balance * 100, 2),
            "trades": len(t), "buy": int((t.side == "BUY").sum()), "sell": int((t.side == "SELL").sum()),
            "trades/week": round(len(t) / max(weeks, 1e-9), 1),
            "TP full": int((t.outcome == "TP").sum()), "SL": int((t.outcome == "SL").sum()),
            "BE": int((t.outcome == "BE").sum()),
            "Friday/other": int((~t.outcome.isin(["TP", "SL", "BE"])).sum()),
            "win %": round((t.pnl > 0).mean() * 100, 1), "R": round(t.r.sum(), 1),
            "PF": round(gains / losses, 2) if losses else float("inf"),
            "max DD %": round(((peak - eq) / peak).max() * 100, 1), "balance": round(eq[-1], 2),
            "_avg_r": t.r.mean(), "_losing_streak": best}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forward", action="store_true")
    ap.add_argument("--since", default="2026-11-19", help="forward: first day of the v2 test")
    ap.add_argument("--journal", default=os.path.join(HERE, "reports", "forward_trades.csv"))
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--balance", type=float, default=4700.0)
    args = ap.parse_args()

    if args.forward:
        t = pd.read_csv(args.journal)
        t["entry_time"] = pd.to_datetime(t.entry_time, utc=True)
        t = t[t.entry_time >= pd.Timestamp(args.since, tz="UTC")]
        t = t.rename(columns={"reason": "outcome"})
        t["outcome"] = t.outcome.map({"tp": "TP", "sl": "SL", "breakeven": "BE"}).fillna("OTHER")
        if t.empty:
            print(f"no forward trades since {args.since} yet")
            return 0
        weeks = max((pd.Timestamp.now(tz="UTC") - pd.Timestamp(args.since, tz="UTC")).days / 7, 1)
        label = f"FORWARD TEST (demo) since {args.since}"
        out = "forward"
    else:
        df = pd.read_csv(os.path.join(HERE, "data", "XAUUSD_M5.csv"))
        df["time"] = pd.to_datetime(df.time, utc=True)
        df = df.set_index("time")[["open", "high", "low", "close"]]
        start = df.index[-1] - pd.DateOffset(months=args.months)
        df = df[df.index >= start - pd.Timedelta(days=2)]       # warm-up candles
        res, _, _ = backtest.run(df, core.Config(), 0.30, balance=args.balance,
                                 spec=dict(stops_level=5, point=0.01))
        t = pd.DataFrame([x.__dict__ for x in res])
        t["entry_time"] = pd.to_datetime(t.entry_time, utc=True)
        t = t[t.entry_time >= start]
        weeks = (df.index[-1] - start).days / 7
        label = (f"BACKTEST v2 (backtest.py, spread 0.30 + gap fills, risk 0.5 %, "
                 f"start {args.balance:.0f}) {start:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d}")
        out = "backtest"

    tab = table(t, args.balance)
    tot = totals(t, args.balance, weeks)
    extra = {k: tot.pop(k) for k in ("_avg_r", "_losing_streak")}
    tab = pd.concat([tab, pd.DataFrame([tot])], ignore_index=True)
    os.makedirs(os.path.join(HERE, "reports"), exist_ok=True)
    tab.to_csv(os.path.join(HERE, "reports", f"monthly_{out}.csv"), index=False)
    pd.set_option("display.width", 250)
    print(label)
    print(tab.to_string(index=False))
    print(f"\naverage {extra['_avg_r']:+.3f} R per trade | longest losing streak {extra['_losing_streak']}"
          f" | profitable months {int((tab['profit %'][:-1] > 0).sum())}/{len(tab) - 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
