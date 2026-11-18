# -*- coding: utf-8 -*-
"""
Stage 9: the versions side by side - same candles, same costs, same engine.

  V1  baseline        v1.0.0 exactly as shipped
  V2  v1 re-tuned     best in-sample config that only changes EXISTING v1
                      parameters (no new filter / exit types)
  V3  new logic       best in-sample config of the whole search (new filters,
                      Friday close, trend, confirmation ... allowed)
  V4  new strategy    best in-sample non-SMC strategy (stage 4)
  V5  final           the configuration shipped as v2.0.0 (research/final.py)

For each: IS / OOS / full at realistic costs, full at stress costs, Monte Carlo
of the full-period trade list, and the monthly table of V1 and V5.
"""
import json

from common import *
from research import alt_strategies as A, cache
from research.final import FINAL_CFG
from research.v2 import params_from_config
from s3_search import to_params

V1_KEYS = dict(p_trend_tf="", p_confirm="none", p_friday_close=0, p_close_session_end=False,
               p_time_stop=0, p_max_touch=0)


def s3_pick(only_v1_params: bool):
    full = pd.read_csv(f"{OUT}/s3_search_expanded.csv", keep_default_na=False)
    full = full[full.score > -99]
    if only_v1_params:
        for k, v in V1_KEYS.items():
            col = full[k].astype(str)
            full = full[col == str(v)]
    best = full.sort_values("score", ascending=False).iloc[0]
    structs = json.load(open(f"{OUT}/s2_structs.json"))
    posts = json.load(open(f"{OUT}/s3_posts.json"))
    return structs[int(best.si)], posts[int(best.pi)]


def alt_pick():
    df = pd.read_csv(f"{OUT}/s4_alternatives.csv").sort_values("score", ascending=False)
    return df.iloc[0]["name"]


def alt_trades(name, m, costs):
    f = name.split()
    fam = f[0]
    kv = {}
    for tok in f[1:]:
        if tok in ("range", "mid"):
            kv["stop"] = tok
            continue
        for key in ("start", "look", "atr", "ema", "len", "cut", "sw", "rr", "be", "tr", "w", "r"):
            if tok.startswith(key):
                kv[key] = tok[len(key):]
                break
    base = replace(E.Params(sl_buffer_pips=1.0, be_r=0, max_per_day=3, min_sl_pips=5, max_sl_pips=400,
                            sess_start=0, sess_end=1440), **costs)
    tr = kv.get("tr", "-").replace("-against", "")
    tr = "" if tr == "-" else tr
    w0, w1 = (int(x) for x in kv["w"].split("-")) if "w" in kv else (480, 1020)
    if fam == "DONCHIAN":
        sig = A.donchian(m, int(kv["look"]), float(kv["atr"]), w0, w1, tr)
        base = replace(base, sl_buffer_pips=0)
    elif fam == "PULLBACK":
        sig = A.pullback(m, int(kv["ema"]), int(kv["sw"]), w0, w1, tr or "h1")
    elif fam == "ORB":
        sig = A.orb(m, int(kv["start"]), int(kv["len"]), int(kv["cut"]), kv["stop"], tr)
    else:
        r0, r1 = (int(x) for x in kv["r"].split("-"))
        sig = A.sweep(m, r0, r1, w0, w1, tr, "against" in name)
    p = replace(base, rr=float(kv["rr"]), be_r=float(kv["be"]))
    return E.simulate(sig, m, p, np.zeros(len(sig), int))


def smc_trades(struct, post_or_params, m, costs):
    cfg = cache.struct_cfg(**struct)
    c = cache.get(cfg)
    if isinstance(post_or_params, dict):
        p = replace(to_params(post_or_params), **costs)
    else:
        p = replace(post_or_params, **costs)
    return E.run(c, m, p)


def main():
    m = mkt()
    final_struct = {k: getattr(FINAL_CFG, k) for k in cache.STRUCT}
    s_v2, p_v2 = s3_pick(True)
    s_v3, p_v3 = s3_pick(False)
    alt = alt_pick()
    versions = {
        "V1 baseline (v1.0.0)": lambda costs: smc_trades({}, E.Params(), m, costs),
        "V2 v1 params re-tuned": lambda costs: smc_trades(s_v2, p_v2, m, costs),
        "V3 search best (new logic)": lambda costs: smc_trades(s_v3, p_v3, m, costs),
        f"V4 best new strategy": lambda costs: alt_trades(alt, m, costs),
        "V5 FINAL v2.0.0": lambda costs: smc_trades(final_struct, params_from_config(FINAL_CFG), m, costs),
    }
    rows, mc_rows = [], []
    trades_final = trades_base = None
    for name, fn in versions.items():
        for cost in ("real", "stress", "base"):
            t = fn(E.COSTS[cost])
            for pn, per in (("IS", IS), ("OOS", OOS), ("FULL", FULL)):
                if cost != "real" and pn != "FULL":
                    continue
                rows.append(dict(version=name, cost=cost, period=pn, **row(name, t, per)))
            if cost == "real":
                tf = cut(t, FULL)
                mc = M.monte_carlo(tf.r.to_numpy(), runs=5000)
                mc_rows.append(dict(version=name, **{k: round(v, 2) for k, v in mc.items()}))
                if name.startswith("V5"):
                    trades_final = tf
                if name.startswith("V1"):
                    trades_base = tf
    df = pd.DataFrame(rows).drop(columns=["name"])
    df.to_csv(f"{OUT}/s9_versions.csv", index=False)
    mc = pd.DataFrame(mc_rows)
    mc.to_csv(f"{OUT}/s9_monte_carlo.csv", index=False)
    M.monthly(trades_final).to_csv(f"{OUT}/s9_monthly_final.csv", index=False)
    M.monthly(trades_base).to_csv(f"{OUT}/s9_monthly_baseline.csv", index=False)
    trades_final.to_csv(f"{OUT}/s9_trades_final.csv", index=False)
    with open(f"{OUT}/s9_versions_config.json", "w") as fh:
        json.dump({"V2": {"struct": s_v2, "post": p_v2}, "V3": {"struct": s_v3, "post": p_v3},
                   "V4": alt}, fh, indent=1, default=str)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
    cols = ["version", "cost", "period", "trades", "per_week", "win", "avg_r", "total_r", "pf", "ret",
            "dd_pct", "dd_r", "sharpe", "sortino", "calmar", "t_stat", "max_losses", "pos_months", "months"]
    print(df[cols].to_string(index=False))
    print()
    print(mc.to_string(index=False))
    print("\nV2:", s_v2, p_v2, "\nV3:", s_v3, p_v3, "\nV4:", alt)


if __name__ == "__main__":
    main()
