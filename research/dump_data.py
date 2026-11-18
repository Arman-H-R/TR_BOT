# -*- coding: utf-8 -*-
"""Save the broker's XAUUSD history (M1, M5, M15, H1) to data/XAUUSD_<tf>.csv.
The research reads these files, so results stay reproducible after MT5 moves on."""
import os
import MetaTrader5 as mt5
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not mt5.initialize():
    raise SystemExit(f"MT5 did not start: {mt5.last_error()}")
try:
    for tf in ("M1", "M5", "M15", "H1"):
        r = mt5.copy_rates_from_pos("XAUUSD", getattr(mt5, "TIMEFRAME_" + tf), 1, 99999)
        df = pd.DataFrame(r)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.to_csv(os.path.join(ROOT, "data", f"XAUUSD_{tf}.csv"), index=False)
        print(tf, len(df), df.time.iloc[0], "..", df.time.iloc[-1])
finally:
    mt5.shutdown()
