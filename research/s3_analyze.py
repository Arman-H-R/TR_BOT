# -*- coding: utf-8 -*-
"""
Stage 3b: what the big search says - and how much of it is noise.

1. Does in-sample rank predict out-of-sample at all? (Spearman correlation of
   IS score vs OOS avg R over every config, and OOS of the IS top-N.)
2. Parameter importance, IN-SAMPLE ONLY: for each parameter value, the median
   IS score of configs that use it, and its share among the IS top 2%.
3. The IS winner, its OOS result and the OOS of its nearest neighbours (a
   plateau or a spike?).
"""
import json

from scipy.stats import spearmanr

from common import *
from s3_search import POST

raw = pd.read_csv(f"{OUT}/s3_search.csv")
structs = json.load(open(f"{OUT}/s2_structs.json"))
posts = json.load(open(f"{OUT}/s3_posts.json"))
ok = raw[raw.score > -99].copy()
print(f"{len(raw)} configs, {len(ok)} trade >= 2/week in-sample")

rho, p = spearmanr(ok.score, ok.oos_avg)
print(f"\n1) Spearman(IS score, OOS avg R) = {rho:+.3f} (p={p:.3g})")
ok = ok.sort_values("score", ascending=False)
for top in (1, 10, 50, 200, 1000, len(ok)):
    g = ok.head(top)
    print(f"   IS top {top:>6}: IS avg R {g.is_avg.mean():+.3f} | OOS avg R {g.oos_avg.mean():+.3f}, "
          f"OOS>0 {100 * (g.oos_avg > 0).mean():.0f}%, OOS trades/cfg {g.oos_n.mean():.0f}")

# expand parameters
def expand(row):
    d = dict(structs[int(row.si)])
    q = dict(posts[int(row.pi)])
    q["sess"] = str(tuple(q["sess"]))
    d.update({f"p_{k}": v for k, v in q.items()})
    return d
params = pd.DataFrame([expand(r) for r in ok.itertuples()], index=ok.index)
full = pd.concat([ok, params], axis=1)
full.to_csv(f"{OUT}/s3_search_expanded.csv", index=False)

print("\n2) parameter importance (in-sample only): median IS score by value, share in IS top 2%")
cut_ = full.score.quantile(0.98)
imp = []
for col in params.columns:
    for v, g in full.groupby(col):
        if len(g) < 50:
            continue
        imp.append(dict(param=col, value=str(v), n=len(g), med_is=g.score.median(),
                        top2=100 * (g.score >= cut_).mean(), med_oos=g.oos_avg.median()))
imp = pd.DataFrame(imp)
imp["spread"] = imp.groupby("param").med_is.transform(lambda s: s.max() - s.min())
imp = imp.sort_values(["spread", "param", "med_is"], ascending=[False, True, False])
imp.round(3).to_csv(f"{OUT}/s3_importance.csv", index=False)
pd.set_option("display.width", 200)
print(imp.round(3).to_string(index=False))

print("\n3) the IS winner and its neighbourhood")
best = full.iloc[0]
print(best[["si", "pi", "score", "is_n", "is_avg", "is_R", "is_dd", "oos_n", "oos_avg", "oos_R", "oos_dd"]].to_string())
print("params:", {k: best[k] for k in params.columns})
same_post = full[full.pi == best.pi]
same_struct = full[full.si == best.si]
print(f"   same filters/exits, other structures: IS {same_post.score.median():+.2f} OOS avg {same_post.oos_avg.median():+.3f}")
print(f"   same structure, other filters/exits : IS {same_struct.score.median():+.2f} OOS avg {same_struct.oos_avg.median():+.3f}")
