# -*- coding: utf-8 -*-
"""Waits for the main pipeline (s3 -> s4 -> s5) AND stage 6 (multi-year) to finish,
then: stage 5 again (adds the multi-year finalists; finished work is skipped),
the one-time final test (s9) and the report tables (s10)."""
import os, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG = os.path.join(ROOT, "reports", "research_v3")
rd = lambda f: open(os.path.join(LOG, f), errors="ignore").read() if os.path.exists(os.path.join(LOG, f)) else ""
pl = open(os.path.join(LOG, "pipeline.log"), "a")
def note(msg):
    pl.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"); pl.flush()
note("tail: waiting for s5 and s6")
import sqlite3
def finished(stage):
    """stage complete according to the experiment store's progress table"""
    try:
        con = sqlite3.connect(os.path.join(LOG, "experiments.sqlite"), timeout=60)
        r = con.execute("SELECT done, total FROM progress WHERE stage=?", (stage,)).fetchone()
        con.close()
        return bool(r) and r[0] >= r[1]
    except Exception:
        return False
while True:
    main_done = finished("s5_wf7")              # last step of stage 5
    s6_done = finished("s6_L_M15_mtf")          # last step of stage 6
    if main_done and s6_done:
        break
    time.sleep(60)
for name, cmd in [("s5b", ["research/v3/s5_validate.py"]), ("s9", ["research/v3/s9_final.py"]),
                  ("s10", ["research/v3/s10_report.py"])]:
    note(f"start {name}")
    with open(os.path.join(LOG, f"{name}.log"), "a") as f:
        rc = subprocess.call([sys.executable, "-u"] + cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
    note(f"end {name} rc={rc}")
    if rc:
        break
