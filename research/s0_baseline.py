# -*- coding: utf-8 -*-
"""Stage 0: the shipped strategy, IS / OOS / full, three cost levels."""
from common import *
from research import cache
import smc_core as core

m = mkt()
c = cache.get(core.Config())
rows = []
for cost, kw in E.COSTS.items():
    t = E.run(c, m, replace(E.Params(), **kw))
    for pname, per in (("IS", IS), ("OOS", OOS), ("FULL", FULL)):
        rows.append(dict(cost=cost, period=pname, **row("baseline", t, per)))
df = pd.DataFrame(rows)
df.to_csv(f"{OUT}/s0_baseline.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
print(df[["cost","period","trades","per_week","win","avg_r","total_r","pf","ret","dd_pct","dd_r","sharpe","sortino","t_stat","max_losses","pos_months","months","tp","sl","be"]].to_string(index=False))
