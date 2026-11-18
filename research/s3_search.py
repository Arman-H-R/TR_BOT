# -*- coding: utf-8 -*-
"""Stage 3: random search over structure x filter x exit parameters.

Selection uses IN-SAMPLE only (common.score). OOS numbers are stored next to
each row so the overfitting gap can be studied afterwards, but they are never
used to rank. Every structure gets the same list of filter/exit draws, so
structures are compared like for like."""
import json, random, sys, time
from multiprocessing import Pool
from common import *
from research import cache

POST = {
    "rr": [2.0, 2.5, 3.0, 4.0, 5.0, 6.0],
    "be_r": [0.0, 1.0, 1.5, 2.0],
    "trail_start": [0.0, 0.0, 2.0, 3.0],
    "sl_buffer_pips": [2.0, 4.0, 8.0, 12.0],
    "min_quality": [0, 1, 2, 3],
    "confirm": ["none", "close_out"],
    "trend_tf": ["", "h1", "h4", "d1"],
    "sess": [(480, 1020), (480, 720), (480, 660), (420, 720), (480, 840)],
    "max_per_day": [2, 3, 5],
    "max_touch": [0, 2],
    "min_sl_pips": [4.0, 10.0, 20.0],
    "time_stop": [0, 96],
    "close_session_end": [False, True],
    "friday_close": [0, 0, 960],
}

def to_params(d):
    d = dict(d)
    s0, s1 = d.pop("sess")
    tf = d.pop("trend_tf")
    return replace(E.Params(), sess_start=s0, sess_end=s1, trend_tf=tf,
                   trend_mode="with" if tf else "off", **d, **E.COSTS["real"])

def draws(n, seed=5):
    rng = random.Random(seed)
    out = [dict(rr=4.0, be_r=1.0, trail_start=0.0, sl_buffer_pips=4.0, min_quality=1, confirm="none",
                trend_tf="", sess=(480, 1020), max_per_day=3, max_touch=0, min_sl_pips=4.0, time_stop=0,
                close_session_end=False, friday_close=0)]
    out += [{k: rng.choice(v) for k, v in POST.items()} for _ in range(n)]
    return out

_m = None
def work(args):
    global _m
    si, struct, posts = args
    if _m is None:
        _m = mkt()
    c = cache.get(cache.struct_cfg(**struct))
    rows = []
    for pi, pd_ in enumerate(posts):
        t = E.run(c, _m, to_params(pd_))
        sc, s_is = score(t)
        s_oos = M.stats(cut(t, OOS), weeks=weeks(OOS))
        blocks = [round(cut(t, b).r.sum(), 2) for b in IS_BLOCKS]
        mon = pd.to_datetime(t.entry_time, utc=True).dt.strftime("%Y-%m")
        rr_ = t.r.astype(float)
        g = rr_.groupby(mon.values)
        mS = g.sum().round(4).to_dict()
        mQ = (rr_ ** 2).groupby(mon.values).sum().round(4).to_dict()
        mN = g.size().to_dict()
        rows.append(dict(si=si, pi=pi, score=sc, is_n=s_is["trades"], is_avg=s_is["avg_r"],
                         is_R=s_is["total_r"], is_dd=s_is["dd_r"], is_t=s_is["t_stat"],
                         blocks=blocks, oos_n=s_oos["trades"], oos_avg=s_oos["avg_r"],
                         oos_R=s_oos["total_r"], oos_dd=s_oos["dd_r"], oos_t=s_oos["t_stat"],
                         oos_pf=s_oos["pf"], mS=json.dumps(mS), mQ=json.dumps(mQ),
                         mN=json.dumps(mN)))
    return rows

if __name__ == "__main__":
    n_post = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    structs = json.load(open(f"{OUT}/s2_structs.json"))
    posts = draws(n_post)
    json.dump(posts, open(f"{OUT}/s3_posts.json", "w"))
    t0 = time.time()
    rows = []
    with Pool(15) as pool:
        for k, r in enumerate(pool.imap_unordered(work, [(i, s, posts) for i, s in enumerate(structs)]), 1):
            rows += r
            if k % 15 == 0:
                print(f"  {k}/{len(structs)} structures, {time.time()-t0:.0f}s", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(f"{OUT}/s3_search.csv", index=False)
    print("done", len(df), f"{time.time()-t0:.0f}s")
