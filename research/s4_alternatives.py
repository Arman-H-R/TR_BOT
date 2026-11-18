# -*- coding: utf-8 -*-
"""Stage 4: completely different strategies, same engine, same costs.
Every grid point is scored in-sample; the IS-best of each family is then shown
out-of-sample (and on 4 years of M15 for the time-based ones)."""
import itertools, time
from common import *
from research import alt_strategies as A

def sim(sig, m, p):
    return E.simulate(sig, m, p, np.zeros(len(sig), int))

def evaluate(name, sig, m, p):
    t = sim(sig, m, p)
    sc, si = score(t)
    so = M.stats(cut(t, OOS), weeks=weeks(OOS))
    return dict(family=name.split(" ")[0], name=name, score=round(sc, 3), is_n=si["trades"],
                is_avg=round(si["avg_r"], 3), is_R=round(si["total_r"], 1), is_dd=round(si["dd_r"], 1),
                oos_n=so["trades"], oos_avg=round(so["avg_r"], 3), oos_R=round(so["total_r"], 1),
                oos_dd=round(so["dd_r"], 1), oos_t=round(so["t_stat"], 2))

if __name__ == "__main__":
    m = mkt()
    real = replace(E.Params(sl_buffer_pips=1.0, be_r=0, max_per_day=3, min_sl_pips=5,
                            max_sl_pips=400, sess_start=0, sess_end=1440), **E.COSTS["real"])
    rows = []
    t0 = time.time()
    exits = [dict(rr=r, be_r=b) for r in (1, 1.5, 2, 3, 4) for b in (0, 1)]
    for st, ln, cut_, stop, tr in itertools.product((480, 570), (15, 30, 60), (720, 900), ("range", "mid"), ("", "h1", "h4")):
        sig = A.orb(m, st, ln, cut_, stop, tr)
        for ex in exits:
            rows.append(evaluate(f"ORB start{st} len{ln} cut{cut_} {stop} tr{tr or '-'} rr{ex['rr']} be{ex['be_r']}",
                                 sig, m, replace(real, **ex)))
    for (r0, r1), (w0, w1), tr, ag in itertools.product(((0, 480), (120, 480), (180, 570)), ((480, 660), (480, 720), (570, 780)), ("", "h1", "h4"), (False, True)):
        if not tr and ag:
            continue
        sig = A.sweep(m, r0, r1, w0, w1, tr, ag)
        for ex in exits:
            rows.append(evaluate(f"SWEEP r{r0}-{r1} w{w0}-{w1} tr{tr or '-'}{'-against' if ag else ''} rr{ex['rr']} be{ex['be_r']}",
                                 sig, m, replace(real, **ex)))
    for fast, sw, (w0, w1), tr in itertools.product((20, 50), (6, 12), ((480, 720), (480, 1020)), ("h1", "h4")):
        sig = A.pullback(m, fast, sw, w0, w1, tr)
        for ex in exits:
            rows.append(evaluate(f"PULLBACK ema{fast} sw{sw} w{w0}-{w1} tr{tr} rr{ex['rr']} be{ex['be_r']}",
                                 sig, m, replace(real, **ex)))
    for look, k, (w0, w1), tr in itertools.product((24, 48, 96), (1.5, 2.5), ((480, 720), (480, 1020)), ("", "h4")):
        sig = A.donchian(m, look, k, w0, w1, tr)
        for ex in exits:
            rows.append(evaluate(f"DONCHIAN look{look} atr{k} w{w0}-{w1} tr{tr or '-'} rr{ex['rr']} be{ex['be_r']}",
                                 sig, m, replace(real, sl_buffer_pips=0, **ex)))
    df = pd.DataFrame(rows).sort_values("score", ascending=False)
    df.to_csv(f"{OUT}/s4_alternatives.csv", index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 70)
    print(f"{len(df)} variants in {time.time()-t0:.0f}s")
    for fam, g in df.groupby("family"):
        print(f"\n{fam}: {len(g)} variants, IS avg R median {g.is_avg.median():+.3f}, "
              f"share with IS avg>0 {100*(g.is_avg>0).mean():.0f}%, OOS avg R median {g.oos_avg.median():+.3f}, "
              f"share OOS>0 {100*(g.oos_avg>0).mean():.0f}%")
        print(g.head(5).drop(columns="family").to_string(index=False))
