# -*- coding: utf-8 -*-
"""Runs stages 2 -> 3 -> 4 -> 5 one after another (each one resumes: finished
experiments are skipped). Start detached so it survives the editor session:
    powershell Start-Process python -ArgumentList research/v3/pipeline.py -WindowStyle Hidden"""
import os, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG = os.path.join(ROOT, "reports", "research_v3")
STAGES = [("s2", ["research/v3/s2_combined.py", "3000"]), ("s3", ["research/v3/s3_timeframes.py", "1500"]),
          ("s4", ["research/v3/s4_mtf.py", "1200"]), ("s5", ["research/v3/s5_validate.py"])]
start = sys.argv[1] if len(sys.argv) > 1 else "s2"
go = False
with open(os.path.join(LOG, "pipeline.log"), "a") as pl:
    for name, cmd in STAGES:
        go = go or name == start
        if not go:
            continue
        pl.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} start {name}\n"); pl.flush()
        with open(os.path.join(LOG, f"{name}.log"), "a") as f:
            rc = subprocess.call([sys.executable, "-u"] + cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
        pl.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} end {name} rc={rc}\n"); pl.flush()
        if rc:
            break
