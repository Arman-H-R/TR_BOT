# -*- coding: utf-8 -*-
"""
Stage 4 - multi-timeframe architectures (dataset A, train/val only).

Roles (see core3.py): context TF(s) -> setup TF (swing/BOS/OB/impulse/pullback/
liquidity) -> entry TF confirmation -> execution TF (M5 fills and exits).
Swing, BOS, OB, impulse and pullback stay on ONE timeframe: smc_core defines
the order block as the last opposite candle before the BOS impulse and counts
impulse/pullback in those same candles, so splitting them is undefined. The
separable components are structure/direction, liquidity, POI (order block)
confirmation, entry confirmation and execution - each gets its own TF here.

4a  enumeration: for the best validated setup config of each setup TF (stage
    2/3), every context TF above it x {ema, bos, liq, ob} singles, pairs
    (mandatory "all" and optional "any"), all four; two-context stacks
    (structure on one TF + liquidity/POI on another); lower-TF entry
    confirmation (close_out / choch / color, 30-120 min) for setups above M5.
4b  Optuna: architecture + the exit/filter parameters re-optimised jointly.
"""
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from research.v3.runner import run_specs  # noqa: E402
from research.v3.search import run_study  # noqa: E402
from research.v3 import core3 as C, select  # noqa: E402

SETUPS = ["M5", "M15", "M30", "H1"]
CTX_TFS = ["M15", "M30", "H1", "H4", "D1"]
KINDS = ["ema", "bos", "liq", "ob"]
SOURCE = {"M5": ["s2_m5_tstat", "s2_m5_total_r"], "M15": ["s3_M15_tstat"],
          "M30": ["s3_M30_tstat"], "H1": ["s3_H1_tstat"]}
N = int(sys.argv[1]) if len(sys.argv) > 1 else 1200


def base_setups(k=2):
    """The k most robust (min of train/val t) setup configs per setup TF, plus v2."""
    out = {"M5": [("v2", {})]}
    for tf, stages in SOURCE.items():
        try:
            for j, sp in enumerate(select.best_specs(stages, n=k)):
                out.setdefault(tf, []).append((f"{tf}#{j + 1}", sp["cfg"]))
        except Exception as e:                       # stage not run yet
            print("no source for", tf, e)
    return out


def ctx_options(setup):
    tfs = [t for t in CTX_TFS if C.MIN[t] > C.MIN[setup]]
    opts = [([], "all", None)]
    for tf in tfs:
        for k in KINDS:
            opts.append(([[k, tf]], "all", tf))
        for a, b in itertools.combinations(KINDS, 2):
            for mode in ("all", "any"):
                opts.append(([[a, tf], [b, tf]], mode, tf))
        opts.append(([[k, tf] for k in KINDS], "all", tf))
    # stacks: structure on a higher TF, liquidity / POI on a lower context TF
    for a, b in itertools.combinations(tfs, 2):
        hi, lo = (a, b) if C.MIN[a] > C.MIN[b] else (b, a)
        for s_kind in ("ema", "bos"):
            for l_kind in ("liq", "ob"):
                opts.append(([[s_kind, hi], [l_kind, lo]], "all", f"{hi}+{lo}"))
    return opts


def entries(setup):
    if setup == "M5":
        return [dict(entry="none")]
    out = [dict(entry="none")]
    for e in ("close_out", "choch", "color"):
        for w in (30, 60, 120):
            if w >= C.MIN[setup]:
                out.append(dict(entry=e, entry_wait=w))
    return out


def enumeration():
    specs = []
    for setup, bases in base_setups().items():
        for name, cfg in bases:
            for ctx, mode, tag in ctx_options(setup):
                for ent in entries(setup):
                    arch = dict(setup=setup, exec="M5", ctx=ctx, ctx_mode=mode, **ent)
                    n_tf = len({setup, "M5"} | {c[1] for c in ctx})
                    lab = (f"{name} ctx={json.dumps(ctx)}/{mode} entry={ent.get('entry')}"
                           f"/{ent.get('entry_wait', 0)}")
                    specs.append(dict(label=lab, cfg=cfg, arch=arch, dataset="A", costs="real",
                                      base=name, n_tf=n_tf))
    return specs


def builder(setup, cfg0):
    tfs = [t for t in CTX_TFS if C.MIN[t] > C.MIN[setup]]

    def build(trial):
        ca, fl = trial.suggest_categorical, trial.suggest_float
        ctx = []
        for k in KINDS:
            tf = ca(f"ctx_{k}", ["none"] + tfs)
            if tf != "none":
                ctx.append([k, tf])
        arch = dict(setup=setup, exec="M5", ctx=ctx, ctx_mode=ca("ctx_mode", ["all", "any"]),
                    ctx_k=trial.suggest_int("ctx_k", 1, 4),
                    ctx_sweep_bars=trial.suggest_int("ctx_sweep_bars", 2, 12),
                    ctx_ob_valid=trial.suggest_int("ctx_ob_valid", 10, 80, step=10),
                    ctx_ema=ca("ctx_ema_span", [20, 50, 100, 200]))
        if setup != "M5":
            arch["entry"] = ca("entry", ["none", "close_out", "choch", "color"])
            arch["entry_wait"] = ca("entry_wait", [30, 60, 120, 240])
        cfg = dict(cfg0)
        cfg.update(rr=fl("rr", 1.5, 8.0, step=0.5),
                   be_trigger_r=ca("be_trigger_r", [1.0, 1.5, 2.0, 2.5, 3.0, 99.0]),
                   min_quality=trial.suggest_int("min_quality", 0, 6),
                   entry_confirm_close=ca("entry_confirm_close", [True, False]),
                   max_trades_per_day=trial.suggest_int("max_trades_per_day", 1, 6))
        return dict(cfg=cfg, arch=arch, n_tf=len({setup, "M5"} | {c[1] for c in ctx}))
    return build


if __name__ == "__main__":
    run_specs(enumeration(), "s4_mtf_enum")
    for setup, bases in base_setups(k=1).items():
        name, cfg0 = bases[0]
        run_study(f"s4_{setup}_mtf", builder(setup, cfg0), N, objective="tstat", seed=37)
