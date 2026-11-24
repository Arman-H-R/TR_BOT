# -*- coding: utf-8 -*-
"""Stage 11: which trading session / hours are best - v1 and v2, last 12 months,
realistic costs, 1 % risk. Windows are New York local time (DST-proof); the UTC
column is the winter (Nov-Mar) equivalent, summer is one hour earlier."""
from common import *
from research import cache
from research.final import FINAL_CFG
from research.v2 import params_from_config

PERIOD = ("2025-11-18", "2026-11-19")
HALVES = [("2025-11-18", "2026-05-19"), ("2026-05-19", "2026-11-19")]
RISK = 1.0
# name: (start NY minutes, end NY minutes, UTC winter label)
SESSIONS = {
    "Asia": (19 * 60, 3 * 60, "00-08"),
    "London": (3 * 60, 11 * 60, "08-16"),
    "New York (now)": (8 * 60, 17 * 60, "13-22"),
    "London + NY": (3 * 60, 17 * 60, "08-22"),
    "Asia + London": (19 * 60, 11 * 60, "00-16"),
    "Asia + London + NY": (19 * 60, 17 * 60, "00-22"),
    "London open 3h": (3 * 60, 6 * 60, "08-11"),
    "London-NY overlap": (8 * 60, 11 * 60, "13-16"),
    "NY morning": (8 * 60, 12 * 60, "13-17"),
    "NY afternoon": (12 * 60, 17 * 60, "17-22"),
}


def run_one(c, m, p):
    t = cut(E.run(c, m, p), PERIOD)
    s = M.stats(t, RISK, weeks=weeks(PERIOD))
    h = [cut(t, x).r.sum() for x in HALVES]
    return t, dict(trades=s["trades"], per_week=round(s["per_week"], 1), buy=s["buys"], sell=s["sells"],
                   win=round(s["win"], 1), avg_r=round(s["avg_r"], 3), total_r=round(s["total_r"], 1),
                   profit_pct=round(s["ret"], 1), max_dd_pct=round(s["dd_pct"], 1), pf=round(s["pf"], 2),
                   tp=s["tp"], sl=s["sl"], be=s["be"], pos_months=f"{s['pos_months']}/{s['months']}",
                   half1_R=round(h[0], 1), half2_R=round(h[1], 1))


def main():
    m = mkt()
    versions = {"v1": (cache.get(core_cfg_v1()), replace(E.Params(), **E.COSTS["real"])),
                "v2": (cache.get(FINAL_CFG), params_from_config(FINAL_CFG, **E.COSTS["real"]))}
    rows, hours = [], []
    for v, (c, base) in versions.items():
        for name, (a, b, utc) in SESSIONS.items():
            wd = (0, 1, 2, 3, 4, 6) if a > b else (0, 1, 2, 3, 4)   # Sunday-evening Asia belongs to Monday
            _, r = run_one(c, m, replace(base, sess_start=a, sess_end=b, weekdays=wd))
            rows.append(dict(version=v, session=name, utc_winter=utc, **r))
        # hour by hour: trade all day, then group by the NY hour of entry
        t, _ = run_one(c, m, replace(base, sess_start=0, sess_end=1440, weekdays=(0, 1, 2, 3, 4, 6)))
        ny_h = t.entry_time.dt.tz_convert("America/New_York").dt.hour
        for hh, g in t.groupby(ny_h.values):
            hours.append(dict(version=v, ny_hour=hh, utc_winter=(hh + 5) % 24, trades=len(g),
                              win=round(100 * (g.r > 0).mean(), 1), avg_r=round(g.r.mean(), 3),
                              total_r=round(g.r.sum(), 1)))
    df, hr = pd.DataFrame(rows), pd.DataFrame(hours)
    df.to_csv(f"{OUT}/s11_sessions.csv", index=False)
    hr.to_csv(f"{OUT}/s11_hours.csv", index=False)
    pd.set_option("display.width", 250)
    print(df.to_string(index=False))
    print()
    print(hr.pivot(index=["ny_hour", "utc_winter"], columns="version", values=["trades", "avg_r", "total_r"]).to_string())


def core_cfg_v1():
    from research.final import V1_CHANGES
    import smc_core as core
    return replace(core.Config(), **V1_CHANGES)


if __name__ == "__main__":
    main()
