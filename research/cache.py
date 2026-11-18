# -*- coding: utf-8 -*-
"""Candidate cache: stage 1 is slow, so every structure config is computed once
and kept in research/cache/<hash>.pkl (git-ignored)."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict, replace

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import smc_core as core  # noqa: E402
from research import engine as E  # noqa: E402

CACHE = os.path.join(HERE, "cache")

# structure fields: changing any of them changes which touches exist at all
STRUCT = ["swing_lookback", "bos_max_age", "ob_search_bars", "ob_pick", "ob_use_body",
          "sl_use_wick", "ob_require_small_body", "ob_max_body_ratio", "ob_valid_bars",
          "max_impulse_bars", "min_pullback_bars", "pullback_extra_bars",
          "cancel_on_opposite_break", "lq_tolerance_pips", "lq_min_touches",
          "lq_lookback_bars", "lq_require_between_bos", "lq_max_distance_pips",
          "lq_sweep_within_bars", "lq_allow_pre_ob"]


def struct_cfg(**changes) -> core.Config:
    return replace(core.Config(), **changes)


def key_of(cfg: core.Config, tf: str) -> str:
    d = {k: getattr(cfg, k) for k in STRUCT}
    d["tf"] = tf
    return hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


def get(cfg: core.Config, tf: str = "M5", df=None) -> pd.DataFrame:
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, key_of(cfg, tf) + ".pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    if df is None:
        df = E.load(tf)
    c = E.candidates_fast(df, cfg)
    c.to_pickle(path)
    return c


def _worker(args):
    changes, tf = args
    cfg = struct_cfg(**changes)
    get(cfg, tf)
    return key_of(cfg, tf)


def build_many(list_of_changes, tf="M5", procs=14):
    from multiprocessing import Pool
    todo = [(ch, tf) for ch in list_of_changes
            if not os.path.exists(os.path.join(CACHE, key_of(struct_cfg(**ch), tf) + ".pkl"))]
    if todo:
        with Pool(procs) as pool:
            for k, _ in enumerate(pool.imap_unordered(_worker, todo), 1):
                if k % 10 == 0 or k == len(todo):
                    print(f"  candidates {k}/{len(todo)}", flush=True)
