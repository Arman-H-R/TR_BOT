# -*- coding: utf-8 -*-
"""Stage 2a: candidate sets for many STRUCTURE variants (cached)."""
import json, random, sys
from common import *
from research import cache

SPACE = {
    "swing_lookback": [1, 2, 3, 4, 5],
    "ob_valid_bars": [40, 60, 80, 120, 160],
    "pullback_extra_bars": [0, 2, 4, 6],
    "min_pullback_bars": [1, 3, 5],
    "lq_tolerance_pips": [5.0, 10.0, 15.0, 20.0, 30.0],
    "lq_min_touches": [2, 3],
    "lq_lookback_bars": [30, 50, 80, 120],
    "lq_require_between_bos": [True, False],
    "lq_allow_pre_ob": [False, True],
    "ob_pick": ["last", "first", "extreme", "biggest"],
    "ob_use_body": [False, True],
    "ob_search_bars": [10, 20, 30],
    "lq_sweep_within_bars": [0, 10, 20, 40],
    "lq_max_distance_pips": [0.0, 50.0, 100.0, 200.0],
}

def one_at_a_time():
    out = [{}]
    for k, vals in SPACE.items():
        for v in vals:
            out.append({k: v})
    return out

def random_structs(n, seed=11):
    rng = random.Random(seed)
    return [{k: rng.choice(v) for k, v in SPACE.items()} for _ in range(n)]

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    structs = one_at_a_time() + random_structs(n)
    with open(f"{OUT}/s2_structs.json", "w") as f:
        json.dump(structs, f)
    cache.build_many(structs, procs=15)
    print("done", len(structs))
