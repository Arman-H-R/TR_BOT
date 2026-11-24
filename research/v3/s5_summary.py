# -*- coding: utf-8 -*-
"""Stage 5 evidence per finalist (train/val only), matched by exact exp_id to
the frozen finalists.json, and the pre-registered ranking written BEFORE s9."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import numpy as np, pandas as pd
from research.v3 import store, core3 as C
from research.v3.runner import clean
from research.v3.s5_validate import VAL_SPLITS, neighbours

OUT = store.OUT
fin = json.load(open(os.path.join(OUT, "finalists.json")))
res = store.frame()
res = res[res.status == "ok"]
R = res.pivot_table(index="exp_id", columns="split", values=["total_r", "trades", "t_stat", "pf",
                    "max_dd_pct", "net_profit_pct", "win_rate", "expectancy"], aggfunc="first")
R.columns = [f"{a}_{b}" for a, b in R.columns]
mc = pd.read_csv(os.path.join(OUT, "s5_montecarlo.csv"))
rows = []
for name, sp in fin.items():
    eid = store.exp_id(clean(dict(sp, label=name, _splits=VAL_SPLITS, stage="s5_fixed")))
    if eid not in R.index:
        print("missing", name); continue
    r = R.loc[eid]
    wf = [r.get(f"total_r_wf{k}_oos", np.nan) for k in range(1, C.WF_FOLDS + 1)]
    blk = [r[c] for c in R.columns if c.startswith("total_r_blk")]
    nb = []
    for k, mult, spn in neighbours(sp):
        e2 = store.exp_id(clean(dict(spn, label=f"{name} | {k} {mult:+d}", _splits=["trainval"],
                                     finalist=name, param=k, step=mult, stage="s5_stab")))
        if e2 in R.index:
            nb.append(R.loc[e2, "total_r_trainval"])
    nb = np.array(nb, float)
    m = mc[mc.candidate == name].set_index("test")
    arch = sp.get("arch", {})
    rows.append(dict(
        candidate=name, setup=arch.get("setup", "M5"),
        ctx=json.dumps(arch.get("ctx") or []), entry=arch.get("entry", "none"),
        trades_tr=r.trades_train, R_tr=r.total_r_train, t_tr=r.t_stat_train,
        trades_val=r.trades_val, R_val=r.total_r_val, t_val=r.t_stat_val, pf_val=r.pf_val,
        dd_trval=r.max_dd_pct_trainval, R_trval=r.total_r_trainval,
        wf_pos=int(np.nansum(np.array(wf) > 0)), wf_R=float(np.nansum(wf)),
        blk_pos=int(sum(b > 0 for b in blk)), blk_min=float(min(blk)),
        stab_n=len(nb), stab_pos=float((nb > 0).mean() * 100) if len(nb) else np.nan,
        stab_keep=float(np.median(nb) / r.total_r_trainval * 100) if len(nb) and r.total_r_trainval else np.nan,
        mc_ploss_boot=m.loc["bootstrap", "p_loss"] if "bootstrap" in m.index else np.nan,
        mc_ploss_stress=m.loc["exec_stress", "p_loss"] if "exec_stress" in m.index else np.nan,
        mc_dd95=m.loc["shuffle", "dd_p95"] if "shuffle" in m.index else np.nan))
t = pd.DataFrame(rows)
# pre-registered composite score (train/val evidence only):
#   robust t = min(t_train, t_val); consistency = WF months + blocks positive;
#   stability = median neighbour keeps x % of R; MC = P(loss) under execution stress
t["robust_t"] = t[["t_tr", "t_val"]].min(axis=1)
z = lambda s: (s - s.mean()) / (s.std() or 1)
t["score"] = (z(t.robust_t) + z(t.wf_pos + t.blk_pos) + z(t.stab_keep.fillna(0).clip(-100, 150))
              - z(t.mc_ploss_stress.fillna(100)))
t = t.sort_values("score", ascending=False)
t.to_csv(os.path.join(OUT, "s5_summary.csv"), index=False)
pd.set_option("display.width", 300)
print(t.round(2).drop(columns=["ctx"]).to_string(index=False))
