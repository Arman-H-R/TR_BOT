# -*- coding: utf-8 -*-
"""The configuration shipped as v2.0.0 = smc_core.Config() defaults.
v1.0.0 differed in: be_trigger_r=1.0, entry_confirm_close=False, friday_close=None."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import smc_core as core
FINAL_CFG = core.Config()
V1_CHANGES = dict(be_trigger_r=1.0, entry_confirm_close=False, friday_close=None)
