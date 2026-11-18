# -*- coding: utf-8 -*-
"""Stage 6: the same rule set on M15, Aug 2022 .. Nov 2026, year by year.
Not the traded timeframe - a check that the ideas hold across market regimes
(2022 range, 2023-24 rally start, 2025-26 parabolic phase)."""
from common import *
from research import cache
import smc_core as core

m = mkt("M15")
c = cache.get(core.Config(), tf="M15")
base = replace(E.Params(), **E.COSTS["real"])
VARIANTS = {
    "shipped": {},
    "friday16": dict(friday_close=960),
    "trend H1": dict(trend_tf="h1", trend_mode="with"),
    "trend H4": dict(trend_tf="h4", trend_mode="with"),
    "against H1": dict(trend_tf="h1", trend_mode="against"),
    "close_out": dict(confirm="close_out"),
    "sess 8-12": dict(sess_end=720),
    "trendH1+fri": dict(trend_tf="h1", trend_mode="with", friday_close=960),
    "trendH1+fri+close_out": dict(trend_tf="h1", trend_mode="with", friday_close=960, confirm="close_out"),
}
YEARS = [("2022-09-01", "2023-01-01"), ("2023-01-01", "2024-01-01"), ("2024-01-01", "2025-01-01"),
         ("2025-01-01", "2026-01-01"), ("2026-01-01", "2026-11-19")]
rows = []
for name, ch in VARIANTS.items():
    t = E.run(c, m, replace(base, **ch))
    r = dict(variant=name)
    for a, b in YEARS:
        x = cut(t, (a, b))
        r[a[:4]] = f"{len(x)}/{x.r.sum():+.1f}"
    s = M.stats(cut(t, ("2022-09-01", "2026-11-19")), weeks=weeks(("2022-09-01", "2026-11-19")))
    r.update(trades=s["trades"], avg_r=round(s["avg_r"], 3), total_r=round(s["total_r"], 1),
             pf=round(s["pf"], 2), dd_r=round(s["dd_r"], 1), t=round(s["t_stat"], 2))
    rows.append(r)
df = pd.DataFrame(rows)
df.to_csv(f"{OUT}/s6_m15_history.csv", index=False)
pd.set_option("display.width", 220)
print("cells: trades/total R per calendar year (2022 = Sep-Dec), realistic costs")
print(df.to_string(index=False))
