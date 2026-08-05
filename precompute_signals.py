"""
Precompute signal CSVs for all strategies x configs x years (non-repainting).
Output: signals/<strat>__<cfgid>__<year>.csv  (idx, time, side, price)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategies import STRATS, strat_cfgs, run_strat

YEARS = ["2023", "2024", "2025"]
DATA_PATHS = {
    "2023": "/home/user/uploads/GOLD.i#_M1_2023 to 2024.csv",
    "2024": "/home/user/uploads/GOLD.i#_M1_2024 to 2025.csv",
    "2025": "/home/user/uploads/GOLD.i#_M1 2025 to 2026.csv",
}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals")
os.makedirs(OUT, exist_ok=True)


def load_year(y):
    t, o, h, l, c = [], [], [], [], []
    with open(DATA_PATHS[y]) as f:
        next(f)
        for line in f:
            p = line.rstrip("\r\n").split("\t")
            t.append(p[0] + " " + p[1])
            o.append(float(p[2])); h.append(float(p[3]))
            l.append(float(p[4])); c.append(float(p[5]))
    return t, o, h, l, c


def cfg_id(cfg):
    return json.dumps(cfg, sort_keys=True).replace(" ", "").replace('"', "").replace("{", "").replace("}", "").replace(":", "=").replace(",", "_")


def main():
    total = 0
    t0 = time.time()
    for y in YEARS:
        t, o, h, l, c = load_year(y)
        for name in STRATS:
            for cfg in strat_cfgs(name):
                cid = cfg_id(cfg)
                fname = f"{OUT}/{name}__{cid}__{y}.csv"
                if os.path.exists(fname):
                    total += 1
                    continue
                buy, sell = run_strat(name, cfg, c, h, l, o)
                rows = []
                for i in buy:
                    rows.append((i, t[i], "BUY", c[i]))
                for i in sell:
                    rows.append((i, t[i], "SELL", c[i]))
                rows.sort()
                with open(fname, "w") as f:
                    f.write("idx,time,side,price\n")
                    for r in rows:
                        f.write(f"{r[0]},{r[1]},{r[2]},{r[3]:.2f}\n")
                total += 1
                if total % 20 == 0:
                    print(f"  [{y}] {name} {cid} -> {len(rows)} signals ({time.time()-t0:.0f}s)", flush=True)
    print(f"DONE: {total} signal files in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
