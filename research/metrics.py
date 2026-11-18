# -*- coding: utf-8 -*-
"""research/metrics.py - one place that turns a list of trades into numbers.

Input: a DataFrame with columns r (net R per trade), entry_time, exit_time,
outcome, d. Equity is compounded at `risk` percent per trade.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def equity_curve(r: np.ndarray, risk: float = 0.5, start: float = 1.0) -> np.ndarray:
    return start * np.cumprod(1.0 + risk / 100.0 * np.asarray(r, float))


def max_streak(mask) -> int:
    best = cur = 0
    for x in mask:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


def stats(t: pd.DataFrame, risk: float = 0.5, weeks: float | None = None) -> dict:
    n = len(t)
    out = dict(trades=n)
    if n == 0:
        return dict(trades=0, per_week=0, win=0, avg_r=0, total_r=0, pf=0, ret=0,
                    dd_pct=0, dd_r=0, sharpe=0, sortino=0, calmar=0, t_stat=0,
                    max_losses=0, pos_months=0, months=0, worst_month=0, tp=0, sl=0,
                    be=0, trail=0, other=0, buys=0, sells=0)
    r = t.r.to_numpy(float)
    et = pd.to_datetime(t.entry_time, utc=True)
    if weeks is None:
        weeks = max((et.max() - et.min()).days / 7.0, 1.0)
    eq = equity_curve(r, risk)
    peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    cum = np.cumsum(r)
    rpeak = np.maximum.accumulate(np.r_[0.0, cum])[1:]
    wins, losses = r[r > 0], r[r < 0]
    # daily returns over every NY trading day in range (flat days count as 0)
    xt = pd.to_datetime(t.exit_time, utc=True).dt.tz_convert("America/New_York").dt.normalize()
    daily = pd.Series(risk / 100.0 * r, index=xt).groupby(level=0).sum()
    days = pd.bdate_range(xt.min(), xt.max(), tz="America/New_York")
    daily = daily.reindex(days.normalize(), fill_value=0.0)
    mu, sd = daily.mean(), daily.std(ddof=1)
    dn = daily[daily < 0]
    dsd = math.sqrt((dn ** 2).sum() / max(len(daily), 1))
    month = et.dt.strftime("%Y-%m")
    mr = pd.Series(r, index=month.values).groupby(level=0).sum()
    ret = (eq[-1] - 1.0) * 100
    dd = float(((peak - eq) / peak).max() * 100)
    years = max(weeks / 52.0, 1e-9)
    cagr = (eq[-1] ** (1 / years) - 1) * 100 if eq[-1] > 0 else -100
    oc = t.outcome.value_counts() if "outcome" in t else pd.Series(dtype=int)
    out.update(
        per_week=n / weeks, win=100.0 * (r > 0).mean(), avg_r=float(r.mean()),
        total_r=float(r.sum()),
        pf=float(wins.sum() / -losses.sum()) if len(losses) else math.inf,
        ret=ret, cagr=cagr, dd_pct=dd, dd_r=float((rpeak - cum).max()),
        sharpe=float(mu / sd * math.sqrt(252)) if sd > 0 else 0.0,
        sortino=float(mu / dsd * math.sqrt(252)) if dsd > 0 else 0.0,
        calmar=cagr / dd if dd > 0 else 0.0,
        t_stat=float(r.mean() / r.std(ddof=1) * math.sqrt(n)) if n > 1 and r.std() > 0 else 0.0,
        max_losses=max_streak(r < 0), avg_win=float(wins.mean()) if len(wins) else 0.0,
        avg_loss=float(losses.mean()) if len(losses) else 0.0,
        months=len(mr), pos_months=int((mr > 0).sum()), worst_month=float(mr.min()),
        best_month=float(mr.max()),
        tp=int(oc.get("TP", 0)), sl=int(oc.get("SL", 0)), be=int(oc.get("BE", 0)),
        trail=int(oc.get("TRAIL", 0)),
        other=int(n - oc.get("TP", 0) - oc.get("SL", 0) - oc.get("BE", 0) - oc.get("TRAIL", 0)),
        buys=int((t.d > 0).sum()) if "d" in t else 0, sells=int((t.d < 0).sum()) if "d" in t else 0,
    )
    return out


def monthly(t: pd.DataFrame, risk: float = 0.5) -> pd.DataFrame:
    if not len(t):
        return pd.DataFrame()
    t = t.copy()
    t["month"] = pd.to_datetime(t.entry_time, utc=True).dt.strftime("%Y-%m")
    rows = []
    for mth, g in t.groupby("month"):
        s = stats(g, risk, weeks=4.35)
        rows.append(dict(month=mth, trades=s["trades"], buys=s["buys"], sells=s["sells"],
                         win=s["win"], total_r=s["total_r"], ret=s["ret"], dd_pct=s["dd_pct"],
                         tp=s["tp"], sl=s["sl"], be=s["be"], trail=s["trail"], other=s["other"]))
    return pd.DataFrame(rows)


def monte_carlo(r: np.ndarray, risk: float = 0.5, runs: int = 5000, seed: int = 7,
                block: int = 1) -> dict:
    """Bootstrap the trade sequence (with replacement; block>1 keeps streaks)."""
    rng = np.random.default_rng(seed)
    r = np.asarray(r, float)
    n = len(r)
    rets, dds, streaks = [], [], []
    for _ in range(runs):
        if block <= 1:
            s = r[rng.integers(0, n, n)]
        else:
            starts = rng.integers(0, n - block + 1, n // block + 1)
            s = np.concatenate([r[a:a + block] for a in starts])[:n]
        eq = equity_curve(s, risk)
        peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
        rets.append((eq[-1] - 1) * 100)
        dds.append(((peak - eq) / peak).max() * 100)
        streaks.append(max_streak(s < 0))
    rets, dds = np.array(rets), np.array(dds)
    return dict(ret_p5=np.percentile(rets, 5), ret_p50=np.percentile(rets, 50),
                ret_p95=np.percentile(rets, 95), p_loss=float((rets < 0).mean() * 100),
                dd_p50=np.percentile(dds, 50), dd_p95=np.percentile(dds, 95),
                dd_p99=np.percentile(dds, 99), streak_p95=float(np.percentile(streaks, 95)))
