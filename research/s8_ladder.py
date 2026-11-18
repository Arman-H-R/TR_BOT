# -*- coding: utf-8 -*-
"""Stage 8: add the individually-robust changes one by one (ladder), M5 IS/OOS
and the same ladder on 4 years of M15 as a regime check."""
from common import *
from research import cache
import smc_core as core

STEPS = [
    ("0 shipped", {}),
    ("1 +Friday 16:00 flat", dict(friday_close=960)),
    ("2 +H1 trend", dict(trend_tf="h1", trend_mode="with")),
    ("3 +close-out confirm", dict(confirm="close_out")),
    ("4 +session 08-12", dict(sess_end=720)),
    ("5 +BE at 2R", dict(be_r=2.0)),
]
ALT = [
    ("1+H4 trend", dict(friday_close=960, trend_tf="h4", trend_mode="with")),
    ("1+close-out", dict(friday_close=960, confirm="close_out")),
    ("1+close-out+08-12", dict(friday_close=960, confirm="close_out", sess_end=720)),
    ("1+08-12", dict(friday_close=960, sess_end=720)),
    ("1+q>=2", dict(friday_close=960, min_quality=2)),
    ("1+close-out+q>=2", dict(friday_close=960, confirm="close_out", min_quality=2)),
]

def ladder(tf, periods):
    m = mkt(tf)
    c = cache.get(core.Config(), tf=tf)
    p = replace(E.Params(), **E.COSTS["real"])
    rows, acc = [], {}
    for name, ch in STEPS + ALT:
        if name[0].isdigit() and name[1] == " ":
            acc.update(ch)
            q = replace(p, **acc)
        else:
            q = replace(p, **ch)
        t = E.run(c, m, q)
        r = dict(tf=tf, step=name)
        for pn, per in periods:
            s = M.stats(cut(t, per), weeks=weeks(per))
            r[f"{pn}_n"] = s["trades"]; r[f"{pn}_avg"] = round(s["avg_r"], 3)
            r[f"{pn}_R"] = round(s["total_r"], 1); r[f"{pn}_dd"] = round(s["dd_r"], 1)
        rows.append(r)
    return pd.DataFrame(rows)

if __name__ == "__main__":
    a = ladder("M5", [("IS", IS), ("OOS", OOS)])
    b = ladder("M15", [("y22_23", ("2022-09-01", "2024-01-01")), ("y24", ("2024-01-01", "2025-01-01")),
                       ("y25_26", ("2025-01-01", "2026-11-19"))])
    a.to_csv(f"{OUT}/s8_ladder_m5.csv", index=False); b.to_csv(f"{OUT}/s8_ladder_m15.csv", index=False)
    pd.set_option("display.width", 220)
    print(a.to_string(index=False)); print(); print(b.to_string(index=False))
