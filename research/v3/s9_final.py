# -*- coding: utf-8 -*-
"""
Stage 9 - the ONE look at the TEST split (2026-06-01 .. 2026-11-19), for the
finalists fixed in stage 5. Nothing is re-selected after this.

  * train / val / test / full numbers for every finalist (store stage s9_test)
  * long history (dataset L, M15 execution, 2022-09 .. 2026-11, per year) for
    finalists whose setup TF is M15 or higher
  * M1 diagnostic (2026-08-10 .. 11-19): M5 setups executed on M1 vs on M5, and
    the same rules run on M1 candles
  * reference cross-check: M5 single-TF finalists through backtest.run
  * all 71 Config values for each finalist
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import fields

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pandas as pd  # noqa: E402
import smc_core as core  # noqa: E402
from research.v3 import core3 as C, store  # noqa: E402
from research.v3.runner import run_specs, evaluate_ref  # noqa: E402
from research.v3.s5_validate import is_mtf, mc  # noqa: E402

OUT = store.OUT


def main():
    fin = json.load(open(os.path.join(OUT, "finalists.json")))
    # test split - once
    run_specs([dict(sp, label=n, _splits=["train", "val", "test", "full", "last12"]) for n, sp in fin.items()],
              "s9_test")
    # last 12 months, month by month, for every finalist (the user's table format)
    months = []
    for n, sp in fin.items():
        t = C.cut(C.evaluate(sp), C.SPLITS["A"]["last12"]).sort_values("entry_time")
        bal = 100.0
        for mth, g in t.groupby(t.entry_time.dt.strftime("%Y-%m")):
            start = bal
            for r in g.r:
                bal *= 1 + C.RISK / 100 * r
            months.append({"candidate": n, "month": mth, "profit %": round((bal / start - 1) * 100, 2),
                           "trades": len(g), "buy": int((g.d > 0).sum()), "sell": int((g.d < 0).sum()),
                           "trades/week": round(len(g) / 4.35, 1), "TP full": int((g.outcome == "TP").sum()),
                           "SL": int((g.outcome == "SL").sum()), "BE": int((g.outcome == "BE").sum()),
                           "other": int((~g.outcome.isin(["TP", "SL", "BE"])).sum()),
                           "win %": round((g.r > 0).mean() * 100, 1), "R": round(g.r.sum(), 1)})
        months.append({"candidate": n, "month": "TOTAL 12m", "profit %": round(bal - 100, 2),
                       "trades": len(t), "buy": int((t.d > 0).sum()), "sell": int((t.d < 0).sum()),
                       "trades/week": round(len(t) / 52.2, 1), "TP full": int((t.outcome == "TP").sum()),
                       "SL": int((t.outcome == "SL").sum()), "BE": int((t.outcome == "BE").sum()),
                       "other": int((~t.outcome.isin(["TP", "SL", "BE"])).sum()),
                       "win %": round((t.r > 0).mean() * 100, 1) if len(t) else 0, "R": round(t.r.sum(), 1)})
    pd.DataFrame(months).to_csv(os.path.join(OUT, "s9_last12_monthly.csv"), index=False)
    # long history for setups >= M15
    long_specs = []
    for n, sp in fin.items():
        a = sp.get("arch", {})
        if C.MIN[a.get("setup", "M5")] >= 15:
            a2 = dict(a, exec="M15")
            if a2.get("entry", "none") != "none" and a2["setup"] == "M15":
                a2["entry_wait"] = max(a2.get("entry_wait", 30), 30)
            long_specs.append(dict(sp, arch=a2, dataset="L", label=n,
                                   _splits=list(C.SPLITS["L"])))
        if a.get("setup") in ("H1", "H4"):           # 2017-08 .. 2026-11 on H1 candles
            long_specs.append(dict(sp, arch=dict(a, exec="H1"), dataset="H", label=n + " | H1 data 2017+",
                                   _splits=list(C.SPLITS["H"])))
    if long_specs:
        run_specs(long_specs, "s9_long")
    # M1 diagnostic
    m1 = []
    for n, sp in fin.items():
        a = sp.get("arch", {})
        if a.get("setup", "M5") != "M5":
            continue
        for ex in ("M5", "M1"):
            m1.append(dict(sp, dataset="M1", arch=dict(a, exec=ex), label=f"{n} | M5 setup, {ex} exec",
                           _splits=["m1win"]))
        m1.append(dict(sp, dataset="M1", arch=dict(a, setup="M1", exec="M1"),
                       label=f"{n} | same rules on M1 candles", _splits=["m1win"]))
    run_specs(m1, "s9_m1")
    # Monte Carlo on the test trades and on the full period
    mcs = []
    for n, sp in fin.items():
        t = C.evaluate(sp)
        mcs += [dict(r, period="test") for r in mc(C.cut(t, C.SPLITS["A"]["test"]).r.to_numpy(), n)]
        mcs += [dict(r, period="full") for r in mc(C.cut(t, C.SPLITS["A"]["full"]).r.to_numpy(), n)]
    pd.DataFrame(mcs).round(3).to_csv(os.path.join(OUT, "s9_montecarlo.csv"), index=False)
    # reference cross-check (spread-only costs both sides)
    rows = []
    for n, sp in fin.items():
        if is_mtf(sp) or sp.get("arch", {}).get("setup", "M5") != "M5":
            continue
        tr = C.cut(evaluate_ref(sp), C.SPLITS["A"]["full"])
        fa = C.cut(C.evaluate(dict(sp, costs="base")), C.SPLITS["A"]["full"])
        rows.append(dict(candidate=n, ref_trades=len(tr), ref_R=round(tr.r.sum(), 2),
                         fast_trades=len(fa), fast_R=round(fa.r.sum(), 2)))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "s9_refcheck.csv"), index=False)
    print(pd.DataFrame(rows).to_string(index=False))
    # 71-parameter table
    base = core.Config()
    tab = []
    for f in fields(core.Config):
        row = dict(parameter=f.name, current_v2=str(getattr(base, f.name)))
        for n, sp in fin.items():
            row[n] = str(C.make_cfg(sp.get("cfg", {})).__dict__[f.name])
        tab.append(row)
    arch_rows = {n: json.dumps(sp.get("arch", {})) for n, sp in fin.items()}
    tab.append(dict(parameter="ARCHITECTURE", current_v2='{"setup": "M5"}', **arch_rows))
    pd.DataFrame(tab).to_csv(os.path.join(OUT, "s9_params71.csv"), index=False)


if __name__ == "__main__":
    main()
