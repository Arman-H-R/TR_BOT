# -*- coding: utf-8 -*-
"""
research/v3/store.py - persistent experiment log (SQLite).

Every evaluated configuration is one row per data split in `results`, keyed by
(exp_id, split). exp_id = sha1 of the full experiment spec (stage, architecture,
config changes, engine params, costs, dataset), so re-running a stage skips what
is already stored (dedup + resume after an interruption). Failed experiments are
stored too (status='error', with the traceback). Nothing is ever deleted.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "reports", "research_v3")
os.makedirs(OUT, exist_ok=True)
DB = os.path.join(OUT, "experiments.sqlite")

METRICS = ["trades", "wins", "losses", "be", "win_rate", "net_profit_pct", "total_r", "pf",
           "expectancy", "avg_win", "avg_loss", "max_dd_pct", "max_dd_r", "max_consec_losses",
           "avg_r", "per_week", "t_stat", "pos_months", "months", "buys", "sells", "tp", "sl"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS experiments (
    exp_id TEXT PRIMARY KEY, created TEXT, stage TEXT, version TEXT, label TEXT,
    spec TEXT, arch TEXT, dataset TEXT, costs TEXT, runtime REAL, status TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS results (
    exp_id TEXT, split TEXT, period TEXT, {", ".join(m + " REAL" for m in METRICS)},
    PRIMARY KEY (exp_id, split));
CREATE TABLE IF NOT EXISTS progress (
    stage TEXT PRIMARY KEY, updated TEXT, done INTEGER, total INTEGER, note TEXT);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    return con


def exp_id(spec: dict) -> str:
    return hashlib.sha1(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:20]


def done_ids(con, stage=None) -> set:
    q = "SELECT exp_id FROM experiments WHERE status IN ('ok','error')"
    args = ()
    if stage:
        q += " AND stage=?"
        args = (stage,)
    return {r[0] for r in con.execute(q, args)}


def save(con, spec: dict, rows: dict, runtime: float, status="ok", error=""):
    """rows: split -> (period string, metrics dict)."""
    eid = exp_id(spec)
    con.execute("INSERT OR REPLACE INTO experiments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, time.strftime("%Y-%m-%d %H:%M:%S"), spec.get("stage"), spec.get("version", "v3"),
                 spec.get("label", ""), json.dumps(spec, sort_keys=True, default=str),
                 json.dumps(spec.get("arch", {}), sort_keys=True), spec.get("dataset", ""),
                 json.dumps(spec.get("costs", {})), runtime, status, error))
    for split, (period, m) in rows.items():
        con.execute(f"INSERT OR REPLACE INTO results VALUES (?,?,?,{','.join('?' * len(METRICS))})",
                    (eid, split, period, *[m.get(k) for k in METRICS]))
    return eid


def progress(con, stage, done, total, note=""):
    con.execute("INSERT OR REPLACE INTO progress VALUES (?,?,?,?,?)",
                (stage, time.strftime("%Y-%m-%d %H:%M:%S"), done, total, note))
    con.commit()


def frame(stage=None, split=None):
    """Flat DataFrame: one row per (experiment, split) with the spec columns."""
    import pandas as pd
    con = connect()
    q = ("SELECT e.exp_id, e.stage, e.label, e.spec, e.status, e.runtime, r.* FROM experiments e "
         "LEFT JOIN results r ON e.exp_id = r.exp_id WHERE 1=1")
    args = []
    if stage:
        q += " AND e.stage=?"
        args.append(stage)
    if split:
        q += " AND r.split=?"
        args.append(split)
    df = pd.read_sql_query(q, con, params=args)
    con.close()
    return df.loc[:, ~df.columns.duplicated()]
