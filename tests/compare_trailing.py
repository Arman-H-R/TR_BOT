# -*- coding: utf-8 -*-
"""
compare_trailing.py - break-even only vs the step trailing stop, same candles.

    python tests/compare_trailing.py                  # last 12 months from MT5
    python tests/compare_trailing.py --start 2026-01-01

Writes reports/trailing_summary.csv and reports/trailing_monthly.csv.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backtest  # noqa: E402
import smc_core as core  # noqa: E402

VARIANTS = [
    ("A  now: BE at 1R, TP 4R", dict()),
    ("B  trail 2R->1R, 3R->2R, TP 4R", dict(trail_steps=True)),
    ("C  same trail, TP 5R", dict(trail_steps=True, rr=5.0)),
    ("D  same trail, TP 6R", dict(trail_steps=True, rr=6.0)),
    ("E  same trail, TP 8R", dict(trail_steps=True, rr=8.0)),
]


def stats(t: pd.DataFrame, balance: float) -> dict:
    eq = np.concatenate([[balance], balance + t.pnl.cumsum().to_numpy()])
    peak = np.maximum.accumulate(eq)
    gl = -t.pnl[t.pnl < 0].sum()
    streak = best = 0
    for o in t.outcome:
        streak = streak + 1 if o == "SL" else 0
        best = max(best, streak)
    return dict(
        trades=len(t), win_pct=(t.pnl > 0).mean() * 100,
        tp=int((t.outcome == "TP").sum()), trail=int((t.outcome == "TRAIL").sum()),
        be=int((t.outcome == "BE").sum()), sl=int((t.outcome == "SL").sum()),
        avg_r=t.r.mean(), total_r=t.r.sum(), net=t.pnl.sum(),
        profit_pct=t.pnl.sum() / balance * 100,
        pf=t.pnl[t.pnl > 0].sum() / gl if gl else np.inf,
        max_dd_pct=((peak - eq) / peak).max() * 100, sl_streak=best,
        avg_win_r=t.r[t.r > 0].mean(), end=eq[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-11-01")
    ap.add_argument("--balance", type=float, default=4700.0)
    args = ap.parse_args()

    df, spec = backtest.load_mt5("XAUUSD", "M5", 99000)
    df = df[df.index >= pd.Timestamp(args.start, tz="UTC")]
    spread = spec["spread"]
    print(f"XAUUSD M5 {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d}, "
          f"{len(df)} candles, spread {spread:.2f}, balance {args.balance:.0f}, risk 0.5%\n")

    summary, monthly = [], []
    for label, change in VARIANTS:
        cfg = replace(core.Config(), **change)
        trades, _, _ = backtest.run(df, cfg, spread, balance=args.balance, spec=spec)
        t = pd.DataFrame([x.__dict__ for x in trades])
        summary.append(dict(variant=label, **stats(t, args.balance)))
        t["month"] = pd.to_datetime(t.entry_time, utc=True).dt.strftime("%Y-%m")
        bal = args.balance
        for m, g in t.groupby("month"):
            s = stats(g, bal)
            monthly.append(dict(variant=label[:1], month=m, **s))
            bal = s["end"]

    s = pd.DataFrame(summary)
    m = pd.DataFrame(monthly)
    os.makedirs("reports", exist_ok=True)
    s.to_csv("reports/trailing_summary.csv", index=False)
    m.to_csv("reports/trailing_monthly.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(s.round(3).to_string(index=False))
    print()
    print(m.pivot(index="month", columns="variant", values="profit_pct").round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
