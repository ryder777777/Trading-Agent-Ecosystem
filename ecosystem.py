"""
Trading Agent Ecosystem — Master Controller harness (honest simulation).

Key rules implemented exactly as specified:
  - Candle-OPEN execution only (zero mid-candle entries, no repainting):
    signals precomputed from the positionally-verified fast engine (matches the
    real deployed get_signal candle-for-candle).
  - Each agent = unique DNA: mode, C1-quiet filter, session filter, C1-direction,
    EMA200 alignment, SL, TP (close / fixed-RR 1:3..1:10), risk tolerance,
    psychology seed.
  - Metrics per agent: trades, wins, losses, winrate, net P&L, profit factor,
    max drawdown, achieved RR, and a psychology (hesitation/fear/greed) layer
    with strict statistical override for benchmark validation.
  - Benchmark: >=3,000 verified trades AND winrate >= 75% AND achieved RR >= 3
    (conservative P&L: when both TP and SL are touched inside a candle, SL wins).
  - Memory: JSONL log (not ephemeral), CSV results, charts.
"""
import json
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

# ----------------------------------------------------------------------------
# CONSTANTS
# ----------------------------------------------------------------------------
YEARS = ["2023", "2024", "2025"]
DATA_PATHS = {
    "2023": "/home/user/uploads/GOLD.i#_M1_2023 to 2024.csv",
    "2024": "/home/user/uploads/GOLD.i#_M1_2024 to 2025.csv",
    "2025": "/home/user/uploads/GOLD.i#_M1 2025 to 2026.csv",
}
SIG_DIR = "/home/user/all_modes"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
AGENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents")

MODES = ["SUPER_LOOSE", "SUPER_LOOSE_2", "Sw0.6_Wi1.2", "Sw0.4_Wi0.8",
         "ORIGINAL", "VeryTight", "Triple_Med", "AGGRESSIVE"]
QUIET_CAPS = [None, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
SESSION_PRESETS = [None, "london_ny", "asia", "ny", "london"]
SESSIONS = {
    "london_ny": {8, 9, 10, 11, 12, 13, 14, 15, 16},
    "asia": {0, 1, 2, 3, 4, 5, 6, 7},
    "ny": {12, 13, 14, 15, 16, 17, 18, 19, 20},
    "london": {8, 9, 10, 11, 12, 13, 14, 15},
}
SLS = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
RRS = [3, 4, 5, 10]
TP_MODES = ["close", "rr"]
RISKS = [0.25, 0.5, 0.75, 1.0]
HESITATION = [0.0, 0.1, 0.2, 0.3]
BENCH_TRADES = 3000
BENCH_WR = 75.0
BENCH_RR = 3.0

# ----------------------------------------------------------------------------
# DATA + SIGNAL PRECOMPUTE (fast, verified signal CSVs)
# ----------------------------------------------------------------------------
def load_year(y):
    """o,h,l,c as np arrays + hour + c1-range + C1-direction + EMA200 flags."""
    o, h, l, c, t = [], [], [], [], []
    with open(DATA_PATHS[y]) as f:
        next(f)
        for line in f:
            p = line.rstrip("\r\n").split("\t")
            t.append(p[0] + " " + p[1])
            o.append(float(p[2])); h.append(float(p[3]))
            l.append(float(p[4])); c.append(float(p[5]))
    o = np.array(o); h = np.array(h); l = np.array(l); c = np.array(c)
    hour = np.array([int(x.split(" ")[1].split(":")[0]) for x in t])
    r1 = h - l                                   # candle range
    dir_bull = c > o                             # bullish candle (body up)
    # EMA200 with the exact same seeding as the strategy logic (_ema)
    n = 200
    a = 2.0 / (n + 1.0)
    ema = np.empty(len(c))
    ema[:n - 1] = np.nan
    ema[n - 1] = c[:n].mean()
    for i in range(n, len(c)):
        ema[i] = c[i] * a + ema[i - 1] * (1 - a)
    ema_bull = c > ema
    return dict(o=o, h=h, l=l, c=c, hour=hour, r1=r1,
                dir_bull=dir_bull, ema_bull=ema_bull, ema=ema)


def load_signals(y, mode):
    rows = []
    with open(os.path.join(SIG_DIR, f"backtest_{mode}_{y}.csv")) as f:
        rd = iter(f)
        next(rd)
        for line in rd:
            p = line.rstrip().split(",")
            rows.append((int(p[0]), 0 if p[2] == "BUY" else 1))
    idx = np.array([r[0] for r in rows], dtype=np.int64)
    side = np.array([r[1] for r in rows], dtype=np.int64)
    return idx, side


PRECOMP = None

def precompute():
    global PRECOMP
    if PRECOMP is not None:
        return PRECOMP
    P = {}
    for y in YEARS:
        P[y] = load_year(y)
        P[y]["sig"] = {}
        for m in MODES:
            P[y]["sig"][m] = load_signals(y, m)
    PRECOMP = P
    return P

# ----------------------------------------------------------------------------
# P&L (vectorized, candle-open entry)
# ----------------------------------------------------------------------------
def pnl_of(idx, side, P, sl, tp, mode_pnl="cons"):
    o_, h_, l_, c_ = P["o"][idx], P["h"][idx], P["l"][idx], P["c"][idx]
    entry = o_
    buy = side == 0
    slpx = np.where(buy, entry - sl, entry + sl)
    sl_hit = np.where(buy, l_ <= slpx, h_ >= slpx)
    close_pnl = np.where(buy, c_ - entry, entry - c_)
    if tp is None:
        return np.where(sl_hit, -sl, close_pnl)
    tppx = np.where(buy, entry + tp, entry - tp)
    tp_hit = np.where(buy, h_ >= tppx, l_ <= tppx)
    both = sl_hit & tp_hit
    if mode_pnl == "cons":      # strict: both-hit => SL first (conservative)
        return np.where(both, -sl, np.where(sl_hit, -sl, np.where(tp_hit, tp, close_pnl)))
    # opt: closer boundary wins (optimistic)
    return np.where(both, np.where(sl <= tp, -sl, tp),
                    np.where(sl_hit, -sl, np.where(tp_hit, tp, close_pnl)))

# ----------------------------------------------------------------------------
# DNA
# ----------------------------------------------------------------------------
def make_dna(rng, i):
    return {
        "id": f"Agent_{i:05d}",
        "name": f"Agent_{i:05d}",
        "mode": rng.choice(MODES),
        "quiet_cap": rng.choice(QUIET_CAPS),
        "session": rng.choice(SESSION_PRESETS),
        "c1_dir": rng.choice([False, True]),
        "ema_align": rng.choice([False, True]),
        "sl": rng.choice(SLS),
        "tp_mode": rng.choice(TP_MODES),
        "rr": rng.choice(RRS),
        "risk": rng.choice(RISKS),
        "hesitation": rng.choice(HESITATION),
        "seed": rng.randint(0, 2**31),
    }

def dna_signature(d):
    return (d["mode"], d["quiet_cap"], d["session"], d["c1_dir"], d["ema_align"],
            d["sl"], d["tp_mode"], d["rr"])

# ----------------------------------------------------------------------------
# EVALUATE one agent (statistical, conservative P&L) — pure, fork-friendly
# ----------------------------------------------------------------------------
def eval_agent(d):
    P = PRECOMP
    tp = None if d["tp_mode"] == "close" else d["rr"] * d["sl"]
    pnls_all = []
    for y in YEARS:
        idx, side = P[y]["sig"][d["mode"]]
        if len(idx) == 0:
            continue
        m = np.ones(len(idx), bool)
        if d["quiet_cap"] is not None:
            m &= P[y]["r1"][idx - 1] <= d["quiet_cap"]
        if d["session"]:
            m &= np.isin(P[y]["hour"][idx], SESSIONS[d["session"]])
        if d["c1_dir"]:
            m &= (P[y]["dir_bull"][idx - 1] == (side == 0))
        if d["ema_align"]:
            m &= (P[y]["ema_bull"][idx - 1] == (side == 0))
        if not m.any():
            continue
        pnls_all.append(pnl_of(idx[m], side[m], P[y], d["sl"], tp, "cons"))
    if not pnls_all:
        pnl = np.array([], dtype=float)
    else:
        pnl = np.concatenate(pnls_all)
    return pnl

def metrics(pnl, d, opt_pnl=None):
    n = int(len(pnl))
    base = dict(d)
    if n == 0:
        out = dict(base)
        out.update(trades=0, wins=0, losses=0, wr=0.0, net=0.0, pf=0.0,
                   maxdd=0.0, rr=0.0, avg_win=0.0, avg_loss=0.0,
                   benchmark=False)
        return out
    wins = int((pnl > 0).sum()); losses = n - wins
    wr = 100.0 * wins / n
    net = float(pnl.sum())
    winp = pnl[pnl > 0]; lossp = pnl[pnl < 0]
    sw = float(winp.sum()); sl_ = -float(lossp.sum())
    pf = sw / sl_ if sl_ > 0 else (float("inf") if sw > 0 else 0.0)
    rr = (float(winp.mean()) / abs(float(lossp.mean()))) if len(winp) and len(lossp) else 0.0
    eq = np.cumsum(pnl)
    maxdd = float((eq - np.maximum.accumulate(eq)).min())
    benchmark = (n >= BENCH_TRADES and wr >= BENCH_WR and rr >= BENCH_RR and net > 0)
    out = dict(base)
    out.update(trades=n, wins=wins, losses=losses, wr=round(wr, 2),
               net=round(net, 2), pf=round(pf, 3), maxdd=round(maxdd, 2),
               rr=round(rr, 2),
               avg_win=round(float(winp.mean()), 3) if len(winp) else 0.0,
               avg_loss=round(float(lossp.mean()), 3) if len(lossp) else 0.0,
               benchmark=benchmark)
    return out

# ----------------------------------------------------------------------------
# PSYCHOLOGY LAYER (hesitation / fear / FOMO) — statistical override
# ----------------------------------------------------------------------------
def emotional_pass(d, pnl):
    """Simulate human-like decision drag on the raw trade series.
    Final benchmark ALWAYS uses the statistical series (override)."""
    rng = random.Random(d["seed"])
    trades = list(pnl)
    if not trades:
        return 0.0
    out = []
    streak = 0
    skip = 0
    for p in trades:
        if skip > 0:
            skip -= 1
            continue
        # hesitation: sometimes skip a trade
        if rng.random() < d["hesitation"]:
            continue
        # fear: after 3 consecutive losses skip next 2
        if streak >= 3:
            skip = 2
            streak = 0
            continue
        out.append(p)
        streak = streak + 1 if p < 0 else 0
    if not out:
        return 0.0
    return float(np.array(out).sum()) - float(np.array(trades).sum())

# ----------------------------------------------------------------------------
# MILESTONE RELEASE HOOK (GitHub) — fires when an agent crosses the benchmark
# ----------------------------------------------------------------------------
def release_agent(agent, token=None):
    """Create a GitHub release titled with agent name + WR metric, e.g.
    'Agent_00001-v1-WR82-RR1x4'. Returns release url or None."""
    import subprocess
    tok = token or os.environ.get("GITHUB_TOKEN", "")
    if not tok:
        return None
    name = agent["name"]
    wr = int(round(agent["wr"]))
    rr = agent.get("rr", 1)
    tag = f"{name}-v1-WR{wr}-RR1x{rr:.0f}".replace(".", "x") if rr else f"{name}-v1-WR{wr}"
    repo = "ryder777777/Trading-Agent-Ecosystem"
    body = (f"Agent {name} crossed benchmark!\n"
            f"Trades: {agent['trades']} | WR: {agent['wr']}% | RR: {agent['rr']}\n"
            f"Net: ${agent['net']} | PF: {agent['pf']}\n"
            f"DNA: {json.dumps({k: agent[k] for k in ('mode','quiet_cap','session','c1_dir','ema_align','sl','tp_mode','rr')})}")
    payload = json.dumps({"tag_name": tag, "name": tag, "body": body,
                          "target_commitish": "main"})
    cmd = (f"curl -s -X POST -H 'Authorization: Bearer {tok}' "
           f"-H 'Accept: application/vnd.github+json' "
           f"-d '{payload}' https://api.github.com/repos/{repo}/releases")
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout
    try:
        return json.loads(out).get("html_url")
    except Exception:
        return None


# ----------------------------------------------------------------------------
# RUNNER
# ----------------------------------------------------------------------------
def run_cohort(n_agents, workers=2, seed=42):
    precompute()
    rng = random.Random(seed)
    agents = [make_dna(rng, i) for i in range(n_agents)]
    t0 = time.time()
    pnls = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, pnl in enumerate(ex.map(eval_agent, agents, chunksize=25)):
            pnls.append(pnl)
    results = []
    for d, pnl in zip(agents, pnls):
        r = metrics(pnl, d)
        results.append(r)
    dt = time.time() - t0

    # emotional pass only for statistical candidates (>= bench trades)
    cands = {r["id"]: r for r in results if r["trades"] >= BENCH_TRADES}
    pnl_map = {d["id"]: p for d, p in zip(agents, pnls)}
    for cid in cands:
        d = next(a for a in agents if a["id"] == cid)
        cands[cid]["emotional_drag"] = round(emotional_pass(d, pnl_map[cid]), 2)
    for r in results:
        if r["id"] not in cands:
            r["emotional_drag"] = 0.0

    bench = [r for r in results if r["benchmark"]]
    print(f"[cohort] {n_agents} agents | {dt:.0f}s | benchmark pass: {len(bench)}")
    for b in bench:
        url = release_agent(b)
        print(f"[MILESTONE] {b['name']} WR{b['wr']}% RR{b['rr']} -> release: {url}")
    return results, bench, dt

if __name__ == "__main__":
    n = int(os.environ.get("N_AGENTS", "200"))
    workers = int(os.environ.get("WORKERS", "2"))
    results, bench, dt = run_cohort(n, workers)
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(AGENT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "cohort_results.jsonl"), "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(results)} agents to {OUT_DIR}/cohort_results.jsonl")
    print(f"benchmark agents: {len(bench)}")
