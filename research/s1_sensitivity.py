# -*- coding: utf-8 -*-
"""Stage 1: one parameter at a time around the shipped strategy (IS and OOS,
realistic costs). Shows what matters before any combined search."""
import time
from common import *
from research import cache
import smc_core as core

m = mkt()
c = cache.get(core.Config())
base = replace(E.Params(), **E.COSTS["real"])
GRID = {
    "rr": [1.5, 2, 2.5, 3, 4, 5, 6, 8],
    "be_r": [0, 0.5, 0.75, 1, 1.5, 2],
    "be_cost_pips": [0, 2, 5, 10],
    "trail_start": [0, 1.5, 2, 3],
    "atr_trail": [0, 2, 3, 5],
    "sl_buffer_pips": [0, 2, 4, 8, 12, 20],
    "min_quality": [0, 1, 2, 3, 4, 5],
    "confirm": ["none", "close_out", "color"],
    "trend_tf": ["", "h1", "h4", "d1"],
    "max_touch": [0, 1, 2],
    "time_stop": [0, 12, 24, 48, 96],
    "max_per_day": [1, 2, 3, 5],
    "side": [0, 1, -1],
    "max_sl_pips": [30, 50, 80, 150],
    "min_sl_pips": [4, 10, 20, 30],
    "entry": ["market", "limit"],
    "amd_mode": ["off", "filter"],
    "close_session_end": [False, True],
    "sess": [(480, 1020), (480, 720), (480, 660), (420, 720), (570, 960), (480, 600), (600, 1020), (180, 1020), (0, 1440)],
}
rows = []
t0 = time.time()
for k, vals in GRID.items():
    for v in vals:
        if k == "sess":
            p = replace(base, sess_start=v[0], sess_end=v[1])
        elif k == "trend_tf":
            p = replace(base, trend_tf=v, trend_mode="with" if v else "off")
        else:
            p = replace(base, **{k: v})
        t = E.run(c, m, p)
        si, so = M.stats(cut(t, IS), weeks=weeks(IS)), M.stats(cut(t, OOS), weeks=weeks(OOS))
        rows.append(dict(param=k, value=str(v), is_n=si["trades"], is_avg=si["avg_r"], is_R=si["total_r"],
                         is_dd=si["dd_r"], is_t=si["t_stat"], oos_n=so["trades"], oos_avg=so["avg_r"],
                         oos_R=so["total_r"], oos_dd=so["dd_r"], oos_t=so["t_stat"]))
    if k == "trend_tf":   # also the counter-trend version
        for v in ["h1", "h4", "d1"]:
            t = E.run(c, m, replace(base, trend_tf=v, trend_mode="against"))
            si, so = M.stats(cut(t, IS), weeks=weeks(IS)), M.stats(cut(t, OOS), weeks=weeks(OOS))
            rows.append(dict(param="against_trend", value=v, is_n=si["trades"], is_avg=si["avg_r"], is_R=si["total_r"],
                             is_dd=si["dd_r"], is_t=si["t_stat"], oos_n=so["trades"], oos_avg=so["avg_r"],
                             oos_R=so["total_r"], oos_dd=so["dd_r"], oos_t=so["t_stat"]))
df = pd.DataFrame(rows).round(3)
df.to_csv(f"{OUT}/s1_sensitivity.csv", index=False)
pd.set_option("display.width", 250)
print(df.to_string(index=False))
print(f"{time.time()-t0:.0f}s")
