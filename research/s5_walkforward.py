# -*- coding: utf-8 -*-
"""
Stage 5: walk-forward test of the SELECTION PROCESS itself.

For each fold the whole search space of stage 3 (every structure x filter x
exit draw) is ranked on the training months only, the winner is traded on the
next test months, and the test months are stitched together. This answers
"if I re-optimised like this every two months, what would I have earned on
data the optimiser had not seen?" - which is the honest number, unlike the
in-sample best.

Uses the per-month sums stored by s3_search.py, so nothing is re-simulated.
"""
import json
import math

from common import *

MONTHS = [f"{y}-{m:02d}" for y, m in
          [(2025, m) for m in range(7, 13)] + [(2026, m) for m in range(1, 12)]]


def folds(train=6, test=2, anchored=False):
    out = []
    k = train
    while k < len(MONTHS):
        tr = MONTHS[(0 if anchored else k - train):k]
        te = MONTHS[k:k + test]
        out.append((tr, te))
        k += test
    return out


def agg(row, months):
    S = sum(row["mS"].get(x, 0.0) for x in months)
    Q = sum(row["mQ"].get(x, 0.0) for x in months)
    N = sum(row["mN"].get(x, 0) for x in months)
    return S, Q, N


def tstat(S, Q, N):
    if N < 2:
        return -99.0
    mean = S / N
    var = max((Q - S * S / N) / (N - 1), 1e-12)
    return mean / math.sqrt(var) * math.sqrt(N)


def pick(df, train, min_per_month=8):
    best, best_sc = None, -1e9
    for row in df:
        S, Q, N = agg(row, train)
        if N < min_per_month * len(train):
            continue
        # consistency: no training month worse than -6R
        worst = min(row["mS"].get(x, 0.0) for x in train)
        sc = tstat(S, Q, N) - (2 if worst < -6 else 0)
        if sc > best_sc:
            best, best_sc = row, sc
    return best, best_sc


def main():
    raw = pd.read_csv(f"{OUT}/s3_search.csv")
    rows = []
    for r in raw.to_dict("records"):
        r["mS"], r["mQ"], r["mN"] = json.loads(r["mS"]), json.loads(r["mQ"]), json.loads(r["mN"])
        rows.append(r)
    base = [r for r in rows if r["pi"] == 0 and r["si"] == 0][0]   # shipped strategy
    out = []
    for anchored in (False, True):
        for tr, te in folds(anchored=anchored):
            win, sc = pick(rows, tr)
            S, Q, N = agg(win, te)
            bS, bQ, bN = agg(base, te)
            out.append(dict(mode="anchored" if anchored else "rolling", train=f"{tr[0]}..{tr[-1]}",
                            test=f"{te[0]}..{te[-1]}", si=win["si"], pi=win["pi"],
                            train_score=round(sc, 2), test_n=N, test_R=round(S, 2),
                            test_avg=round(S / N, 3) if N else 0, base_n=bN, base_R=round(bS, 2)))
    df = pd.DataFrame(out)
    df.to_csv(f"{OUT}/s5_walkforward.csv", index=False)
    pd.set_option("display.width", 200)
    print(df.to_string(index=False))
    for mode, g in df.groupby("mode"):
        print(f"\n{mode}: walk-forward OOS {g.test_n.sum()} trades, {g.test_R.sum():+.1f} R "
              f"({g.test_R.sum() / max(g.test_n.sum(), 1):+.3f} R/trade); shipped strategy on the "
              f"same months {g.base_n.sum()} trades, {g.base_R.sum():+.1f} R")


if __name__ == "__main__":
    main()
