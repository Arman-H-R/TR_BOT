# -*- coding: utf-8 -*-
"""Stage 10: a handful of SIMPLE candidates (shipped rules + few logical
changes) per 2-month block, realistic + stress costs, M5 and M15."""
from common import *
from research import cache
import smc_core as core

CANDS = {
    "A shipped": {},
    "B +Friday": dict(friday_close=960),
    "C +Friday +closeout": dict(friday_close=960, confirm="close_out"),
    "D +Friday +closeout +q2": dict(friday_close=960, confirm="close_out", min_quality=2),
    "E +Friday +BE2R TP5": dict(friday_close=960, be_r=2.0, rr=5.0),
    "F +Friday +closeout +BE2R TP5": dict(friday_close=960, confirm="close_out", be_r=2.0, rr=5.0),
    "G +Friday +closeout 08-12": dict(friday_close=960, confirm="close_out", sess_end=720),
}
BLOCKS = [(f"{y}-{m:02d}-01", f"{y + (m + 2 > 12)}-{(m + 1) % 12 + 1:02d}-01") for y, m in
          [(2025, 7), (2025, 9), (2025, 11), (2026, 1), (2026, 3), (2026, 5), (2026, 7), (2026, 9)]] + [("2026-11-01", "2026-11-19")]
m = mkt(); c = cache.get(core.Config())
rows = []
for name, ch in CANDS.items():
    for cost in ("real", "stress"):
        t = cut(E.run(c, m, replace(E.Params(), **E.COSTS[cost], **ch)), FULL)
        s = M.stats(t, weeks=weeks(FULL))
        b = [cut(t, x).r.sum() for x in BLOCKS]
        rows.append(dict(cand=name, cost=cost, n=s["trades"], wk=round(s["per_week"], 2), avg=round(s["avg_r"], 3),
                         R=round(s["total_r"], 1), pf=round(s["pf"], 2), dd_r=round(s["dd_r"], 1), dd_pct=round(s["dd_pct"], 1),
                         sharpe=round(s["sharpe"], 2), t=round(s["t_stat"], 2), pos_blocks=f"{sum(x > 0 for x in b)}/{len(b)}",
                         worst_block=round(min(b), 1), blocks=" ".join(f"{x:+.0f}" for x in b)))
df = pd.DataFrame(rows); df.to_csv(f"{OUT}/s10_candidates.csv", index=False)
pd.set_option("display.width", 250)
print(df.to_string(index=False))
