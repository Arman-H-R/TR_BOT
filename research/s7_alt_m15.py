# -*- coding: utf-8 -*-
"""Stage 7: the alternative families on 4 years of M15 (regime check)."""
from common import *
from research import alt_strategies as A

m = mkt("M15")
real = replace(E.Params(sl_buffer_pips=1.0, be_r=0, max_per_day=3, min_sl_pips=5, max_sl_pips=600,
                        sess_start=0, sess_end=1440), **E.COSTS["real"])
YEARS = [("2022-09-01", "2023-01-01"), ("2023-01-01", "2024-01-01"), ("2024-01-01", "2025-01-01"),
         ("2025-01-01", "2026-01-01"), ("2026-01-01", "2026-11-19")]
TESTS = {
    "DONCHIAN 2h atr2.5 8-12 H4 rr4": (A.donchian(m, 8, 2.5, 480, 720, "h4"), dict(sl_buffer_pips=0, rr=4)),
    "DONCHIAN 4h atr1.5 8-17 H4 rr4": (A.donchian(m, 16, 1.5, 480, 1020, "h4"), dict(sl_buffer_pips=0, rr=4)),
    "DONCHIAN 4h atr2.5 8-17 noTr rr3": (A.donchian(m, 16, 2.5, 480, 1020, ""), dict(sl_buffer_pips=0, rr=3)),
    "PULLBACK ema7 sw4 8-17 H4 rr3 be1": (A.pullback(m, 7, 4, 480, 1020, "h4"), dict(rr=3, be_r=1)),
    "PULLBACK ema7 sw4 8-12 H4 rr1.5": (A.pullback(m, 7, 4, 480, 720, "h4"), dict(rr=1.5)),
    "ORB 9:30 60m cut15 H4 rr1.5": (A.orb(m, 570, 60, 900, "range", "h4"), dict(rr=1.5)),
    "ORB 9:30 60m cut12 rr2": (A.orb(m, 570, 60, 720, "range", ""), dict(rr=2)),
}
rows = []
for name, (sig, ex) in TESTS.items():
    t = E.simulate(sig, m, replace(real, **ex), np.zeros(len(sig), int))
    r = dict(strategy=name)
    for a, b in YEARS:
        x = cut(t, (a, b))
        r[a[:4]] = f"{len(x)}/{x.r.sum():+.1f}"
    s = M.stats(cut(t, ("2022-09-01", "2026-11-19")))
    r.update(trades=s["trades"], avg_r=round(s["avg_r"], 3), pf=round(s["pf"], 2), dd_r=round(s["dd_r"], 1), t=round(s["t_stat"], 2))
    rows.append(r)
df = pd.DataFrame(rows)
df.to_csv(f"{OUT}/s7_alt_m15.csv", index=False)
pd.set_option("display.width", 220)
print(df.to_string(index=False))
