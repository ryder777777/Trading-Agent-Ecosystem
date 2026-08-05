"""
Analyze 10k-agent cohort: dedupe by DNA, find best agents, charts, summary.
"""
import json, os, csv
from collections import Counter
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = "/home/user/agent-ecosystem"
rows = [json.loads(l) for l in open(f"{ROOT}/results/cohort_results.jsonl")]
print(f"total agents: {len(rows)}")

# dedupe by DNA signature (identical strategy = identical metrics)
sig = Counter()
for r in rows:
    s = (r["mode"], r["quiet_cap"], r["session"], r["c1_dir"], r["ema_align"],
         r["sl"], r["tp_mode"], r["rr"])
    sig[s] += 1
print(f"unique strategies (DNA signatures): {len(sig)}")

# only agents with trades
tr = [r for r in rows if r["trades"] >= 1]
print(f"agents with >=1 trade: {len(tr)}")
tr3k = [r for r in rows if r["trades"] >= 3000]
print(f"agents with >=3000 trades: {len(tr3k)}")
if tr3k:
    tr3k.sort(key=lambda r: -r["wr"])
    print(f"best WR among >=3000 trades: {tr3k[0]['wr']}% "
          f"({tr3k[0]['name']} {tr3k[0]['mode']} sl={tr3k[0]['sl']} quiet={tr3k[0]['quiet_cap']})")

# Best agents (unique strategies) by criteria
def best_by(key, label, n=8, min_trades=500):
    cand = [r for r in tr if r["trades"] >= min_trades]
    cand.sort(key=lambda r: -r[key])
    print(f"\nTOP {n} by {label} (min {min_trades} trades):")
    for r in cand[:n]:
        print(f"  {r['name']} {r['mode']:<12} quiet={str(r['quiet_cap']):<5} sess={str(r['session']):<10} "
              f"c1dir={int(r['c1_dir'])} ema={int(r['ema_align'])} sl={r['sl']} tp={r['tp_mode']}/{r['rr']} "
              f"-> {r['trades']} tr | WR {r['wr']}% | net ${r['net']} | PF {r['pf']} | RR {r['rr']} | DD {r['maxdd']}")
    return cand[:n]

best_by("wr", "winrate")
best_by("pf", "profit factor", min_trades=1000)
best_by("net", "net P&L", min_trades=1000)

# efficiency frontier: high RR & high WR together?
high = [r for r in tr if r["trades"] >= 1000]
high.sort(key=lambda r: -(r["rr"] * r["wr"]))
print("\nTOP by WR*RR (efficiency, >=1000 trades):")
for r in high[:8]:
    print(f"  {r['name']} WR {r['wr']}% RR {r['rr']} -> score {r['wr']*r['rr']:.0f} | {r['trades']} tr | net ${r['net']} | {r['mode']} sl={r['sl']} quiet={r['quiet_cap']}")

# closest to benchmark
print("\nClosest to benchmark (trades>=3000, sorted by WR desc, need WR>=75 & RR>=3):")
for r in tr3k[:10]:
    print(f"  WR {r['wr']:>5}% RR {r['rr']:>4} trades {r['trades']:>5} net ${r['net']:>8} | {r['mode']} sl={r['sl']} quiet={r['quiet_cap']} sess={r['session']}")

# ---- charts ----
os.makedirs(f"{ROOT}/reports", exist_ok=True)

# Fig 1: WR distribution (agents with >=500 trades)
fig, ax = plt.subplots(figsize=(11, 5))
wrs = [r["wr"] for r in tr if r["trades"] >= 500]
ax.hist(wrs, bins=40, color="#4da3ff", alpha=0.9, edgecolor="white")
ax.axvline(75, color="#e74c3c", ls="--", lw=1.5)
ax.text(75.5, ax.get_ylim()[1]*0.9, "benchmark 75%", color="#e74c3c", fontsize=10)
ax.set_xlabel("Win rate %")
ax.set_ylabel("agents")
ax.set_title("10,000 agents — Win rate distribution (>=500 trades) | 0 agents reached 75%",
             fontsize=12, loc="left")
ax.grid(axis="y", alpha=0.3)
plt.tight_layout()
plt.savefig(f"{ROOT}/reports/WR_distribution.png", dpi=110)
plt.close()

# Fig 2: WR vs RR scatter (efficiency frontier)
fig, ax = plt.subplots(figsize=(11, 6.5))
xs = [r["rr"] for r in tr if r["trades"] >= 500]
ys = [r["wr"] for r in tr if r["trades"] >= 500]
sizes = [max(8, min(90, r["trades"]/300)) for r in tr if r["trades"] >= 500]
cols = [("#2ecc71" if r["net"] > 0 else "#e74c3c") for r in tr if r["trades"] >= 500]
ax.scatter(xs, ys, s=sizes, c=cols, alpha=0.45)
ax.axhline(75, color="#e74c3c", ls="--", lw=1.2)
ax.axvline(3, color="#e74c3c", ls="--", lw=1.2)
ax.text(3.05, 78, "benchmark zone\n(WR>=75% & RR>=3)", color="#e74c3c", fontsize=9)
ax.set_xlabel("Achieved Reward:Risk")
ax.set_ylabel("Win rate %")
ax.set_title("10,000 agents — WR vs RR (bubble=volume, green=profitable) | NO agent in benchmark zone",
             fontsize=11, loc="left")
ax.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(f"{ROOT}/reports/WR_vs_RR.png", dpi=110)
plt.close()

# save best-agents CSV
with open(f"{ROOT}/reports/top_agents.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    for r in sorted(tr, key=lambda r: -r["wr"])[:100]:
        w.writerow(r)
print("\nsaved charts + top_agents.csv")
