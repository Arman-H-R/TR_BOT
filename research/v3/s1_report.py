# -*- coding: utf-8 -*-
"""Stage 1 report: per parameter, every value vs the baseline (train / val)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pandas as pd  # noqa: E402
from research.v3 import store  # noqa: E402


def load():
    df = store.frame("s1_oat")
    df = df[df.status == "ok"]
    sp = df.spec.map(json.loads)
    df["param"] = sp.map(lambda s: s.get("param", "baseline"))
    df["value"] = sp.map(lambda s: s.get("value", "<switch on>" if s.get("param") else "base"))
    df["kind"] = sp.map(lambda s: s.get("kind", "fast"))
    w = df.pivot_table(index=["kind", "param", "value", "label"], columns="split",
                       values=["trades", "total_r", "pf", "max_dd_pct", "win_rate", "net_profit_pct",
                               "expectancy"], aggfunc="first")
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    return w.reset_index()


def main():
    w = load()
    rows = []
    for kind in ("fast", "ref"):
        base = w[(w.kind == kind) & (w.label == "baseline")].iloc[0]
        for param, g in w[(w.kind == kind) & (w.param != "baseline")].groupby("param", sort=False):
            g = g[g.value != "<switch on>"]
            span_tr = g.total_r_train.max() - g.total_r_train.min()
            span_va = g.total_r_val.max() - g.total_r_val.min()
            best_tr = g.loc[g.total_r_train.idxmax()]
            rows.append(dict(kind=kind, param=param, n_values=len(g),
                             span_R_train=round(span_tr, 1), span_R_val=round(span_va, 1),
                             best_value_train=best_tr.value, best_R_train=round(best_tr.total_r_train, 1),
                             same_value_R_val=round(best_tr.total_r_val, 1),
                             base_R_train=round(base.total_r_train, 1), base_R_val=round(base.total_r_val, 1),
                             improves_both=bool(best_tr.total_r_train > base.total_r_train + 1
                                                and best_tr.total_r_val > base.total_r_val + 1)))
    imp = pd.DataFrame(rows)
    imp["effect"] = pd.cut(imp[["span_R_train", "span_R_val"]].max(axis=1), [-1, 2, 8, 1e9],
                           labels=["negligible", "moderate", "strong"])
    imp = imp.sort_values(["kind", "span_R_train"], ascending=[True, False])
    out = store.OUT
    w.to_csv(os.path.join(out, "s1_oat_all.csv"), index=False)
    imp.to_csv(os.path.join(out, "s1_oat_impact.csv"), index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    print(imp.to_string(index=False))
    cols = ["param", "value", "trades_train", "total_r_train", "pf_train", "trades_val",
            "total_r_val", "pf_val", "max_dd_pct_val"]
    print()
    print(w[cols + ["kind"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
