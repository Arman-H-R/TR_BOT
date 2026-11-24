# -*- coding: utf-8 -*-
"""
Stage 5 - validation of the candidates (TRAIN + VALIDATION data only).

5a  finalists: chosen from stages 2-4 by explicit rules (select.py)
5b  walk-forward
      fixed   : every finalist's R in each of the 7 monthly out-of-sample folds
      re-opt  : the optimisation PROCEDURE itself - Optuna on 4 months, the
                winner traded the next month (does searching generalise?)
5c  parameter stability: every numeric parameter of a finalist moved one and
    two steps each way; share of neighbours still profitable, median loss
5d  Monte Carlo on the train+val trades (see mc() for the four tests)
Writes reports/research_v3/finalists.json and s5_*.csv.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import smc_core as core  # noqa: E402
from research import metrics as M  # noqa: E402
from research.final import V1_CHANGES  # noqa: E402
from research.v3 import core3 as C, select, store  # noqa: E402
from research.v3.runner import run_specs  # noqa: E402
from research.v3.search import run_study, strategy_space  # noqa: E402

OUT = store.OUT
VAL_SPLITS = (["train", "val", "trainval"] + [f"wf{k}_oos" for k in range(1, C.WF_FOLDS + 1)]
              + [k for k in C.SPLITS["A"] if k.startswith("blk_")])
S2 = ["s2_m5_tstat", "s2_m5_total_r"]
S3 = {tf: [f"s3_{tf}_tstat"] for tf in ("M15", "M30", "H1", "H4")}
S4 = ["s4_mtf_enum"] + [f"s4_{s}_mtf" for s in ("M5", "M15", "M30", "H1")]
# stage 6 (multi-year): stage -> name of its train split
S6 = {"s6_L_M15": "ltrain", "s6_L_M30": "ltrain", "s6_L_H1": "ltrain", "s6_L_M15_mtf": "ltrain",
      "s6_H_H1": "htrain", "s6_H_H4": "htrain"}


def is_mtf(spec):
    a = spec.get("arch", {})
    return bool(a.get("ctx")) or a.get("entry", "none") != "none"


def strip(spec):
    return {k: spec[k] for k in ("cfg", "arch", "dataset", "costs", "origin") if k in spec}


def finalists():
    allst = S2 + sum(S3.values(), []) + S4
    w = select.ranked(allst, "robust")
    w["mtf"] = w.spec.map(lambda s: is_mtf(json.loads(s)))
    w["setup"] = w.spec.map(lambda s: json.loads(s).get("arch", {}).get("setup", "M5"))
    pick = {"v2 baseline (shipped)": dict(cfg={}, arch=dict(setup="M5")),
            "v1 baseline": dict(cfg=dict(V1_CHANGES, friday_close=None), arch=dict(setup="M5"))}
    single = w[~w.mtf]
    for tf in ("M5", "M15", "M30", "H1", "H4"):
        g = single[single.setup == tf]
        if len(g):
            pick[f"best single-TF {tf}"] = strip(json.loads(g.iloc[0].spec))
    if len(w[w.mtf]):
        for k, row in enumerate(w[w.mtf].head(3).itertuples(), 1):
            pick[f"best MTF #{k}"] = strip(json.loads(row.spec))
    prof = select.ranked(allst, "profit")
    pick["highest profit (val R)"] = strip(json.loads(prof.iloc[0].spec))
    tv = select.wide(allst)
    tv = tv[(tv.trades_train >= select.MIN_TRAIN) & (tv.trades_val >= select.MIN_VAL)]
    tv["sum_r"] = tv.total_r_train + tv.total_r_val
    pick["highest historical R (train+val)"] = strip(json.loads(tv.sort_values("sum_r").iloc[-1].spec))
    tv["risk_adj"] = tv.sum_r / tv[["max_dd_r_train", "max_dd_r_val"]].max(axis=1).clip(lower=3)
    pick["best risk-adjusted"] = strip(json.loads(tv.sort_values("risk_adj").iloc[-1].spec))
    # stage 6: best multi-year config per stage (robust = min(t over 2022/2017..2025, t val)).
    # Re-mapped onto dataset A with M5 execution so WF / stability / MC compare like with like.
    for st6, trn in S6.items():
        try:
            r6 = select.ranked(st6, "robust", train=trn, min_train=60, min_val=10)
        except Exception:
            continue
        if len(r6):
            sp = strip(json.loads(r6.iloc[0].spec))
            sp["dataset"] = "A"
            sp["arch"] = dict(sp.get("arch", {}), exec="M5")
            sp["origin"] = st6
            pick[f"multi-year {st6[3:]}"] = sp
    # top robust (not already in) for the stability contest
    for k, row in enumerate(w.head(6).itertuples(), 1):
        sp = strip(json.loads(row.spec))
        if sp not in pick.values():
            pick[f"robust contender #{k}"] = sp
    for v in pick.values():
        v.setdefault("dataset", "A")
        v.setdefault("costs", "real")
    return pick


STEPS = dict(swing_lookback=1, ob_search_bars=5, ob_valid_bars=10, pullback_extra_bars=1,
             min_pullback_bars=1, lq_tolerance_pips=0.25, lq_min_touches=1, lq_lookback_bars=20,
             sl_buffer_pips=0.25, rr=0.5, be_trigger_r=0.5, be_cost_pips=1, min_sl_pips=0.25,
             max_sl_pips=0.25, min_quality=1, max_trades_per_day=1, trail_start_r=0.5,
             trail_gap_r=0.5, lq_sweep_within_bars=5)
FRAC = {"lq_tolerance_pips", "sl_buffer_pips", "min_sl_pips", "max_sl_pips"}
ARCH_STEPS = dict(ctx_k=1, ctx_sweep_bars=2, ctx_ob_valid=10, entry_wait=30)


def neighbours(spec):
    base = core.Config()
    out = []
    for k, st in STEPS.items():
        cur = spec["cfg"].get(k, getattr(base, k))
        if k == "be_trigger_r" and cur >= 99:
            continue
        if k.startswith("trail_") and not spec["cfg"].get("trail_steps"):
            continue
        for mult in (-2, -1, 1, 2):
            if k in FRAC:
                v = round(cur * (1 + st * mult), 1) if cur else (1.0 * mult if mult > 0 else None)
            else:
                v = cur + st * mult
            if v is None or v < 0 or (k in ("swing_lookback", "lq_min_touches", "rr") and v < 1):
                continue
            if isinstance(cur, int) and not isinstance(cur, bool):
                v = int(v)
            cfg = dict(spec["cfg"], **{k: v})
            if k == "ob_valid_bars":
                cfg["bos_max_age"] = max(80, v)
            out.append((k, mult, dict(spec, cfg=cfg)))
    for k, st in ARCH_STEPS.items():
        a = spec.get("arch", {})
        if k not in a and not (k.startswith("ctx_") and a.get("ctx")):
            continue
        cur = a.get(k, {"ctx_k": 2, "ctx_sweep_bars": 6, "ctx_ob_valid": 40}.get(k))
        if cur is None:
            continue
        for mult in (-2, -1, 1, 2):
            v = cur + st * mult
            if v >= 1:
                out.append((k, mult, dict(spec, arch=dict(a, **{k: v}))))
    return out


def mc(r, label):
    """1 sequence shuffle (same trades, new order) -> drawdown / streak risk only
    2 bootstrap (trades drawn with replacement) -> uncertainty of the result
    3 block bootstrap (10-trade blocks) keeps regime clustering
    4 execution stress: every trade -0.05R extra cost and 10 % of winners missed"""
    rng = np.random.default_rng(5)
    r = np.asarray(r, float)
    n = len(r)
    rows = []
    for test in ("shuffle", "bootstrap", "block10", "exec_stress"):
        rets, dds, streaks, exps = [], [], [], []
        for _ in range(5000):
            if test == "shuffle":
                s = rng.permutation(r)
            elif test == "bootstrap":
                s = r[rng.integers(0, n, n)]
            elif test == "block10":
                st = rng.integers(0, max(n - 10, 1), n // 10 + 1)
                s = np.concatenate([r[a:a + 10] for a in st])[:n]
            else:
                s = r[rng.integers(0, n, n)] - 0.05
                miss = (s > 0) & (rng.random(n) < 0.10)
                s = s[~miss]
            eq = M.equity_curve(s, C.RISK)
            pk = np.maximum.accumulate(np.r_[1.0, eq])[1:]
            rets.append((eq[-1] - 1) * 100)
            dds.append(((pk - eq) / pk).max() * 100)
            streaks.append(M.max_streak(s < 0))
            exps.append(s.mean())
        rets, dds = np.array(rets), np.array(dds)
        rows.append(dict(candidate=label, test=test, trades=n, ret_p5=np.percentile(rets, 5),
                         ret_p50=np.percentile(rets, 50), ret_p95=np.percentile(rets, 95),
                         p_loss=(rets < 0).mean() * 100, p_ret_gt_20=(rets > 20).mean() * 100,
                         p_ret_gt_50=(rets > 50).mean() * 100, dd_p50=np.percentile(dds, 50),
                         dd_p95=np.percentile(dds, 95), dd_p99=np.percentile(dds, 99),
                         streak_p50=np.percentile(streaks, 50), streak_p95=np.percentile(streaks, 95),
                         exp_p5=np.percentile(exps, 5), exp_p50=np.percentile(exps, 50)))
    return rows


def wf_reopt(trials=300):
    """Walk-forward of the optimisation procedure (single-TF M5 space)."""
    rows = []
    for k in range(1, C.WF_FOLDS + 1):
        def build(trial, k=k):
            return dict(cfg=strategy_space(trial, "M5"), arch=dict(setup="M5"),
                        _splits=[f"wf{k}_is", f"wf{k}_oos"])
        st = run_study(f"s5_wf{k}", build, trials, objective="tstat", obj_split=f"wf{k}_is", seed=k)
        w = store.frame(f"s5_wf{k}")
        w = w[w.status == "ok"].pivot_table(index="exp_id", columns="split", values=["t_stat", "total_r", "trades"])
        w.columns = [f"{a}_{b}" for a, b in w.columns]
        w = w[w[f"trades_wf{k}_is"] >= 30]
        top = w.sort_values(f"t_stat_wf{k}_is", ascending=False)
        rows.append(dict(fold=k, oos=C.SPLITS["A"][f"wf{k}_oos"][0][:7],
                         best_is_R=top[f"total_r_wf{k}_is"].iloc[0],
                         best_oos_R=top[f"total_r_wf{k}_oos"].iloc[0],
                         best_oos_trades=top[f"trades_wf{k}_oos"].iloc[0],
                         top10_mean_oos_R=top[f"total_r_wf{k}_oos"].head(10).mean()))
    return pd.DataFrame(rows)


def main():
    fin = finalists()
    with open(os.path.join(OUT, "finalists.json"), "w") as f:
        json.dump(fin, f, indent=1)
    print("finalists:", *fin, sep="\n  ")
    # 5b fixed walk-forward + blocks
    specs = [dict(sp, label=name, _splits=VAL_SPLITS) for name, sp in fin.items()]
    run_specs(specs, "s5_fixed")
    # 5c stability
    stab = []
    for name, sp in fin.items():
        for k, mult, nb in neighbours(sp):
            stab.append(dict(nb, label=f"{name} | {k} {mult:+d}", _splits=["trainval"],
                             finalist=name, param=k, step=mult))
    run_specs(stab, "s5_stab")
    # 5d Monte Carlo on train+val trades
    mcs = []
    for name, sp in fin.items():
        t = C.cut(C.evaluate(sp), C.SPLITS["A"]["trainval"])
        mcs += mc(t.r.to_numpy(), name)
    pd.DataFrame(mcs).round(3).to_csv(os.path.join(OUT, "s5_montecarlo.csv"), index=False)
    # walk-forward re-optimisation
    wf = wf_reopt()
    wf.round(2).to_csv(os.path.join(OUT, "s5_wf_reopt.csv"), index=False)
    print(wf.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
