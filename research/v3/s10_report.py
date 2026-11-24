# -*- coding: utf-8 -*-
"""Stage 10 - collect every stage into reports/research_v3/REPORT_TABLES.md
(numbers only; the narrative lives in research/v3/RESULTS_V3.md)."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from research.v3 import core3 as C, select, store  # noqa: E402

OUT = store.OUT


def md(df: pd.DataFrame, floatfmt=2) -> str:
    if df is None or not len(df):
        return "_(none)_\n"
    df = df.copy()
    for c in df.columns:
        if df[c].dtype.kind == "f":
            df[c] = df[c].round(floatfmt)
    cols = [str(c) for c in df.columns]
    out = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    for row in df.itertuples(index=False):
        out += "| " + " | ".join("" if (isinstance(v, float) and np.isnan(v)) else str(v) for v in row) + " |\n"
    return out


def avg_dd(r, risk=C.RISK):
    eq = np.cumprod(1 + risk / 100 * np.asarray(r, float))
    pk = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    dd = (pk - eq) / pk * 100
    return float(dd[dd > 0].mean()) if (dd > 0).any() else 0.0


def main():
    parts = ["# SMC research v3 - generated tables\n"]
    con = store.connect()
    st = pd.read_sql_query("SELECT stage, status, COUNT(*) n, ROUND(SUM(runtime)/60,1) cpu_min "
                           "FROM experiments GROUP BY stage, status ORDER BY stage", con)
    parts += ["## Experiment log (every run, incl. failed / skipped)\n", md(st)]
    err = pd.read_sql_query("SELECT stage, label, substr(error, -240) error FROM experiments "
                            "WHERE status != 'ok'", con)
    parts += ["### Failed / skipped experiments\n", md(err)]

    # stage 1
    imp = os.path.join(OUT, "s1_oat_impact.csv")
    if os.path.exists(imp):
        parts += ["## Stage 1 - one parameter at a time (train / val total R)\n", md(pd.read_csv(imp))]

    # stages 2-4 summaries
    cols = ["label", "trades_train", "total_r_train", "pf_train", "t_stat_train", "trades_val",
            "total_r_val", "pf_val", "t_stat_val", "max_dd_pct_val", "robust"]
    for title, stages in [("Stage 2 - M5 combined search", ["s2_m5_tstat", "s2_m5_total_r"]),
                          ("Stage 3 - M15", ["s3_M15_tstat"]), ("Stage 3 - M30", ["s3_M30_tstat"]),
                          ("Stage 3 - H1", ["s3_H1_tstat"]), ("Stage 3 - H4", ["s3_H4_tstat"]),
                          ("Stage 4 - MTF enumeration", ["s4_mtf_enum"]),
                          ("Stage 4 - MTF search M5 setup", ["s4_M5_mtf"]),
                          ("Stage 4 - MTF search M15 setup", ["s4_M15_mtf"]),
                          ("Stage 4 - MTF search M30 setup", ["s4_M30_mtf"]),
                          ("Stage 4 - MTF search H1 setup", ["s4_H1_mtf"])]:
        try:
            w = select.wide(stages)
            ok = w[(w.trades_train >= select.MIN_TRAIN) & (w.trades_val >= select.MIN_VAL)]
            head = (f"{len(w)} configs, {len(ok)} with >= {select.MIN_TRAIN}/{select.MIN_VAL} trades; "
                    f"share with val R > 0: {(ok.total_r_val > 0).mean() * 100:.0f} %; "
                    f"corr(train R, val R) = {ok.total_r_train.corr(ok.total_r_val):.2f}\n\n")
            r = select.ranked(stages, "robust")
            r["label"] = r.label.str.slice(0, 70)
            parts += [f"## {title}\n", head, "Top 8 by robust score min(t_train, t_val):\n\n",
                      md(r[cols].head(8))]
        except Exception as e:
            parts += [f"## {title}\n", f"_not available: {e}_\n"]

    # stage 4 enumeration: value of each confirmation
    try:
        w = select.wide("s4_mtf_enum")
        sp = w.spec.map(json.loads)
        w["base"] = sp.map(lambda s: s.get("base"))
        w["ctx"] = sp.map(lambda s: json.dumps(s["arch"].get("ctx")))
        w["entry"] = sp.map(lambda s: s["arch"].get("entry", "none"))
        w["n_tf"] = sp.map(lambda s: s.get("n_tf"))
        w["kinds"] = sp.map(lambda s: "+".join(sorted({c[0] for c in s["arch"].get("ctx") or []})) or "none")
        w["mode"] = sp.map(lambda s: s["arch"].get("ctx_mode"))
        g = (w.groupby(["kinds", "mode"]).agg(n=("exp_id", "size"), trades_val=("trades_val", "median"),
                                              R_train=("total_r_train", "median"),
                                              R_val=("total_r_val", "median"), pf_val=("pf_val", "median"))
             .reset_index().sort_values("R_val", ascending=False))
        parts += ["## Stage 4 - effect of each HTF confirmation (median over bases / TFs)\n", md(g)]
        g = (w.groupby("entry").agg(n=("exp_id", "size"), trades_val=("trades_val", "median"),
                                    R_train=("total_r_train", "median"), R_val=("total_r_val", "median"))
             .reset_index())
        parts += ["### Lower-TF entry confirmation\n", md(g)]
        g = (w.groupby("n_tf").agg(n=("exp_id", "size"), best_R_val=("total_r_val", "max"),
                                   median_R_val=("total_r_val", "median"),
                                   median_trades_val=("trades_val", "median")).reset_index())
        parts += ["### By number of timeframes\n", md(g)]
    except Exception as e:
        parts += [f"_stage 4 breakdown not available: {e}_\n"]

    # stage 5
    for name, title in [("s5_montecarlo.csv", "Stage 5 - Monte Carlo (train+val trades, risk 1 %)"),
                        ("s5_wf_reopt.csv", "Stage 5 - walk-forward re-optimisation (procedure test)")]:
        p = os.path.join(OUT, name)
        if os.path.exists(p):
            parts += [f"## {title}\n", md(pd.read_csv(p))]
    try:
        f = select.wide("s5_fixed")
        wf = [c for c in f.columns if c.startswith("total_r_wf")]
        t = f[["label"] + wf].copy()
        t["folds_positive"] = (f[wf] > 0).sum(axis=1).astype(str) + f"/{len(wf)}"
        parts += ["## Stage 5 - fixed walk-forward (R per out-of-sample month)\n", md(t)]
        blk = [c for c in f.columns if c.startswith("total_r_blk")]
        parts += ["### Two-month blocks (R)\n", md(f[["label"] + blk])]
    except Exception as e:
        parts += [f"_walk-forward not available: {e}_\n"]
    p = os.path.join(OUT, "s5_summary.csv")       # exact exp_id matching (s5_summary.py)
    if os.path.exists(p):
        parts += ["## Stage 5 - evidence per finalist (train/val only) and the pre-registered score\n",
                  md(pd.read_csv(p).drop(columns=["ctx"]))]
    # final
    try:
        f = store.frame("s9_test")
        f = f[f.status == "ok"]
        piv = f.pivot_table(index="label", columns="split",
                            values=["trades", "total_r", "pf", "win_rate", "expectancy", "max_dd_pct",
                                    "net_profit_pct", "max_consec_losses"], aggfunc="first")
        piv.columns = [f"{a}_{b}" for a, b in piv.columns]
        piv = piv.reset_index()
        order = ["label"] + [f"{m}_{s}" for s in ("train", "val", "test", "last12") for m in
                             ("trades", "total_r", "pf", "win_rate", "expectancy", "max_dd_pct",
                              "net_profit_pct")]
        parts += ["## FINAL - train / validation / TEST (looked at once) / last 12 months\n",
                  md(piv[[c for c in order if c in piv.columns]])]
        fin = json.load(open(os.path.join(OUT, "finalists.json")))
        rows = []
        for n, sp in fin.items():
            t = C.cut(C.evaluate(sp), C.SPLITS["A"]["last12"])
            m = C.metrics(t, C.SPLITS["A"]["last12"])
            daily = t.groupby(t.exit_time.dt.date).r.sum() * C.RISK / 100
            rows.append(dict(candidate=n, trades=m["trades"], ret_pct=m["net_profit_pct"],
                             win=m["win_rate"], pf=m["pf"], exp_R=m["expectancy"], total_R=m["total_r"],
                             max_dd=m["max_dd_pct"], avg_dd=avg_dd(t.r), losing_streak=m["max_consec_losses"],
                             ret_over_dd=m["net_profit_pct"] / max(m["max_dd_pct"], 0.1),
                             sharpe_daily=float(daily.mean() / daily.std() * np.sqrt(252)) if len(daily) > 2 else 0))
        parts += ["## Last 12 months (2025-11-19 .. 2026-11-19) - summary, risk 1 %\n", md(pd.DataFrame(rows))]
        mm = pd.read_csv(os.path.join(OUT, "s9_last12_monthly.csv"))
        for n, g in mm.groupby("candidate", sort=False):
            parts += [f"### {n} - month by month\n", md(g.drop(columns="candidate"))]
    except Exception as e:
        parts += [f"_final test not available yet: {e}_\n"]
    for name, title in [("s9_montecarlo.csv", "FINAL - Monte Carlo (test / full)"),
                        ("s9_refcheck.csv", "Reference cross-check vs backtest.run")]:
        p = os.path.join(OUT, name)
        if os.path.exists(p):
            parts += [f"## {title}\n", md(pd.read_csv(p))]
    for st_, title in [("s9_long", "Long history 2022-2026 (M15 execution)"), ("s9_m1", "M1 diagnostic")]:
        try:
            f = store.frame(st_)
            f = f[f.status == "ok"][["label", "split", "trades", "total_r", "pf", "win_rate", "max_dd_pct"]]
            parts += [f"## {title}\n", md(f)]
        except Exception as e:
            parts += [f"_{title}: {e}_\n"]
    p = os.path.join(OUT, "s9_params71.csv")
    if os.path.exists(p):
        parts += ["## All 71 parameters per finalist\n", md(pd.read_csv(p))]
    path = os.path.join(OUT, "REPORT_TABLES.md")
    open(path, "w", encoding="utf-8").write("\n".join(parts))
    print("wrote", path)


if __name__ == "__main__":
    main()
