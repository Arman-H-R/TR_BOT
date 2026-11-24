# -*- coding: utf-8 -*-
"""
research/v3/search.py - Optuna (TPE) search with a resumable journal.

The study is optimised on the TRAIN split only. Every trial is also written
to the experiment store with its VALIDATION numbers, which are used later for
candidate selection (never for the optimiser's objective). Trials are asked in
batches and evaluated in a process pool; the Optuna journal file plus the
store make a stopped search continue where it stopped.
"""
from __future__ import annotations

import math
import os
import sys
import time
from multiprocessing import Pool

import optuna
from optuna.storages import JournalStorage
from optuna.storages.journal import JournalFileBackend, JournalFileOpenLock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from research.v3 import core3 as C  # noqa: E402
from research.v3 import store  # noqa: E402
from research.v3.runner import _work, clean  # noqa: E402

optuna.logging.set_verbosity(optuna.logging.WARNING)


def strategy_space(trial, tf="M5"):
    """Config changes for one trial. Candle counts are in setup-TF candles; pip
    distances scale with sqrt(candle minutes / 5) (gold's range grows ~ sqrt(t))."""
    f = math.sqrt(C.MIN[tf] / 5.0)
    s = trial.suggest_int
    fl = trial.suggest_float
    ca = trial.suggest_categorical
    cfg = dict(
        swing_lookback=s("swing_lookback", 1, 5),
        ob_search_bars=s("ob_search_bars", 5, 40, step=5),
        ob_valid_bars=s("ob_valid_bars", 20, 160, step=10),
        pullback_extra_bars=s("pullback_extra_bars", 0, 8),
        min_pullback_bars=s("min_pullback_bars", 0, 8),
        ob_pick=ca("ob_pick", ["last", "first", "extreme", "biggest"]),
        ob_use_body=ca("ob_use_body", [False, True]),
        cancel_on_opposite_break=ca("cancel_on_opposite_break", [False, True]),
        lq_tolerance_pips=round(fl("lq_tolerance_pips", 3, 30) * f, 1),
        lq_min_touches=s("lq_min_touches", 1, 4),
        lq_lookback_bars=s("lq_lookback_bars", 20, 200, step=20),
        lq_require_between_bos=ca("lq_require_between_bos", [True, False]),
        lq_sweep_within_bars=ca("lq_sweep_within_bars", [0, 5, 10, 20, 40]),
        entry_confirm_close=ca("entry_confirm_close", [True, False]),
        sl_buffer_pips=round(fl("sl_buffer_pips", 0, 16) * f, 1),
        rr=fl("rr", 1.5, 8.0, step=0.5),
        be_trigger_r=ca("be_trigger_r", [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 99.0]),
        be_cost_pips=fl("be_cost_pips", 0, 6, step=1),
        trail_steps=ca("trail_steps", [False, True]),
        min_sl_pips=round(fl("min_sl_pips", 0, 30) * f, 1),
        max_sl_pips=round(fl("max_sl_pips", 60, 400, log=True) * f, 0),
        session_start=ca("session_start", ["02:00", "03:00", "05:00", "07:00", "08:00", "09:00"]),
        session_end=ca("session_end", ["12:00", "13:00", "15:00", "17:00", "19:00"]),
        friday_close=ca("friday_close", [None, "12:00", "14:00", "16:00"]),
        min_quality=s("min_quality", 0, 6),
        max_trades_per_day=s("max_trades_per_day", 1, 6),
        trend_filter=ca("trend_filter", ["off", "H1", "H4"]),
        amd_mode=ca("amd_mode", ["off", "filter"]),
    )
    cfg["bos_max_age"] = max(80, cfg["ob_valid_bars"])      # window must hold the OB (audit #2)
    if cfg["trail_steps"]:
        cfg["trail_start_r"] = fl("trail_start_r", 1.0, 3.0, step=0.5)
        cfg["trail_gap_r"] = fl("trail_gap_r", 0.5, 2.0, step=0.5)
    if C.MIN[tf] > 5:
        cfg["ob_small_pips"] = round(20 * f, 1)
    return cfg


def objective_value(m: dict, kind: str, weeks: float, min_per_week: float = 1.0) -> float:
    if not m or m["trades"] < max(30, min_per_week * weeks):
        return -10.0 + (m["trades"] / 100 if m else 0)
    if kind == "tstat":
        return m["t_stat"]
    if kind == "total_r":                          # profit, but a drawdown cap
        return m["total_r"] if m["max_dd_r"] <= 15 else m["total_r"] - 2 * (m["max_dd_r"] - 15)
    raise ValueError(kind)


def run_study(name: str, build_spec, n_trials: int, objective="tstat", procs=14, batch=28,
              seed=1, dataset="A", obj_split="train", enqueue=(), min_per_week=1.0):
    """build_spec(trial) -> spec dict (Config changes + arch)."""
    os.makedirs(os.path.join(store.OUT, "optuna"), exist_ok=True)
    path = os.path.join(store.OUT, "optuna", f"{name}.journal")
    journal = JournalStorage(JournalFileBackend(path, lock_obj=JournalFileOpenLock(path)))
    study = optuna.create_study(study_name=name, storage=journal, direction="maximize",
                                load_if_exists=True,
                                sampler=optuna.samplers.TPESampler(seed=seed, n_startup_trials=200,
                                                                   multivariate=True, group=True,
                                                                   constant_liar=True))
    if not study.trials:
        for params in enqueue:
            study.enqueue_trial(params, skip_if_exists=True)
    # finish trials that a crash left RUNNING
    for t in study.get_trials(deepcopy=False, states=(optuna.trial.TrialState.RUNNING,)):
        study.tell(t.number, state=optuna.trial.TrialState.FAIL)
    done = len([t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE])
    weeks = (lambda a, b: (b - a).days / 7)(*[__import__("pandas").Timestamp(x)
                                             for x in C.SPLITS[dataset][obj_split]])
    con = store.connect()
    print(f"[{name}] {done} trials done, running to {n_trials}", flush=True)
    t0 = time.time()
    with Pool(procs) as pool:
        while done < n_trials:
            k = min(batch, n_trials - done)
            trials = [study.ask() for _ in range(k)]
            specs = []
            for tr in trials:
                sp = build_spec(tr)
                sp.update(stage=name, dataset=dataset, costs=sp.get("costs", "real"),
                          trial=tr.number)
                specs.append(sp)
            for tr, (spec, rows, rt, status, err) in zip(trials, pool.map(_work, specs)):
                store.save(con, clean(spec), rows, rt, status, err)
                if status != "ok":
                    study.tell(tr, state=optuna.trial.TrialState.FAIL)
                    continue
                m = rows[obj_split][1]
                tr.set_user_attr("val", rows.get("val", (None, {}))[1])
                study.tell(tr, objective_value(m, objective, weeks, min_per_week))
            con.commit()
            done += k
            best = study.best_value if study.best_trial else None
            el = time.time() - t0
            print(f"[{name}] {done}/{n_trials}  best {best:.3f}  {el / 60:.1f} min", flush=True)
            store.progress(con, name, done, n_trials, f"best {best:.3f}")
    con.close()
    return study
