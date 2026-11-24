# -*- coding: utf-8 -*-
"""
research/v3/runner.py - parallel, resumable experiment execution.

run_specs(specs, stage) evaluates every spec not yet in the store (dedup by
exp_id), in a process pool; the main process is the only SQLite writer and
commits after every result, so an interrupted stage resumes where it stopped.
Spec kind "ref" runs the slow reference simulator backtest.run() (for Config
fields the fast engine does not model); its costs are the spread only.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from multiprocessing import Pool

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from research.v3 import core3 as C  # noqa: E402
from research.v3 import store  # noqa: E402


def evaluate_ref(spec: dict) -> pd.DataFrame:
    import backtest
    ds = spec.get("dataset", "A")
    cfg = C.make_cfg(spec.get("cfg", {}))
    df = C.bars(C.BASE[ds], ds)[["open", "high", "low", "close"]]
    res, _, _ = backtest.run(df, cfg, 0.30, balance=10000.0, spec=dict(stops_level=5, point=0.01))
    t = pd.DataFrame([x.__dict__ for x in res])
    if t.empty:
        return pd.DataFrame(columns=["r", "entry_time", "exit_time", "outcome", "d"])
    t["entry_time"] = pd.to_datetime(t.entry_time, utc=True)
    t["exit_time"] = pd.to_datetime(t.exit_time, utc=True)
    t["d"] = (t.side == "BUY").astype(int) * 2 - 1
    return t


def _work(spec):
    t0 = time.time()
    ds = spec.get("dataset", "A")
    splits = spec.get("_splits") or C.SEARCH_SPLITS[ds]
    try:
        t = evaluate_ref(spec) if spec.get("kind") == "ref" else C.evaluate(spec)
        rows = {s: (f"{C.SPLITS[ds][s][0]}..{C.SPLITS[ds][s][1]}", C.metrics(t, C.SPLITS[ds][s]))
                for s in splits}
        return spec, rows, time.time() - t0, "ok", ""
    except Exception:
        return spec, {}, time.time() - t0, "error", traceback.format_exc()


def clean(spec):
    return {k: v for k, v in spec.items() if not k.startswith("_")}


def run_specs(specs, stage: str, procs: int = 14, log_every: int = 50):
    con = store.connect()
    done = store.done_ids(con)
    for s in specs:
        s["stage"] = stage
    todo, seen = [], set()
    for s in specs:
        eid = store.exp_id(clean(s))
        if eid in done or eid in seen:
            continue
        seen.add(eid)
        todo.append(s)
    total = len(specs)
    print(f"[{stage}] {total} specs, {total - len(todo)} already stored, {len(todo)} to run", flush=True)
    store.progress(con, stage, total - len(todo), total, "running")
    t0 = time.time()
    n_err = 0
    if todo:
        with Pool(min(procs, len(todo))) as pool:
            for k, (spec, rows, rt, status, err) in enumerate(pool.imap_unordered(_work, todo), 1):
                store.save(con, clean(spec), rows, rt, status, err)
                n_err += status != "ok"
                con.commit()
                if k % log_every == 0 or k == len(todo):
                    el = time.time() - t0
                    print(f"[{stage}] {k}/{len(todo)}  {el / 60:.1f} min, eta "
                          f"{el / k * (len(todo) - k) / 60:.1f} min, errors {n_err}", flush=True)
                    store.progress(con, stage, total - len(todo) + k, total, f"errors {n_err}")
    store.progress(con, stage, total, total, f"done, errors {n_err}")
    con.close()
    return store.frame(stage)
