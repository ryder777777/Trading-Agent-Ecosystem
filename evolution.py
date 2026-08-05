"""
AUTONOMOUS EVOLUTION ENGINE — 24h self-improving backtest loop.

Agents (genomes) evolve continuously:
  - every cycle: elite selection -> crossover + mutation (code mutation) + exploration
  - each genome evaluated on 1.06M real GOLD M1 candles (candle-open, conservative P&L)
  - registry keeps best-known strategies; leaderboards by WR / PF / net / RR / score
  - periodic GitHub auto-commit + Telegram digest + milestone broadcast
  - live HTTP dashboard with progress

Resumable: state saved every commit; restart continues where it left off.
"""
import json
import math
import os
import random
import threading
import time
import subprocess
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
YEARS = ["2023", "2024", "2025"]
DATA_PATHS = {
    "2023": "/home/user/uploads/GOLD.i#_M1_2023 to 2024.csv",
    "2024": "/home/user/uploads/GOLD.i#_M1_2024 to 2025.csv",
    "2025": "/home/user/uploads/GOLD.i#_M1 2025 to 2026.csv",
}
SIG_DIR = "/home/user/all_modes"
ROOT = os.path.dirname(os.path.abspath(__file__))
EVO_DIR = os.path.join(ROOT, "evolution")
MODES = ["SUPER_LOOSE", "SUPER_LOOSE_2", "Sw0.6_Wi1.2", "Sw0.4_Wi0.8",
         "ORIGINAL", "VeryTight", "Triple_Med", "AGGRESSIVE"]
SESSIONS = {
    "london_ny": {8, 9, 10, 11, 12, 13, 14, 15, 16},
    "asia": {0, 1, 2, 3, 4, 5, 6, 7},
    "ny": {12, 13, 14, 15, 16, 17, 18, 19, 20},
    "london": {8, 9, 10, 11, 12, 13, 14, 15},
}
BENCH_TRADES, BENCH_WR, BENCH_RR = 3000, 75.0, 3.0
OBJ_LIST = ["score", "wr", "pf", "rr"]   # rotating optimization objectives

# ----------------------------------------------------------------------------
# PRECOMPUTE (per year): OHLC + features + signals
# ----------------------------------------------------------------------------
PREC = None

def _ema(vals, n):
    a = 2.0 / (n + 1.0)
    out = np.empty(len(vals)); out[:n-1] = np.nan
    out[n-1] = vals[:n].mean()
    for i in range(n, len(vals)):
        out[i] = vals[i] * a + out[i-1] * (1 - a)
    return out

def _atr(h, l, c, n=14):
    out = np.empty(len(c)); out[0] = h[0] - l[0]
    for i in range(1, len(c)):
        tr = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
        out[i] = tr if i < n else (out[i-1]*(n-1)+tr)/n
    return out

def precompute():
    global PREC
    if PREC is not None:
        return PREC
    P = {}
    for y in YEARS:
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
        r1 = h - l
        dir_bull = c > o
        sigs = {}
        for m in MODES:
            rows = []
            with open(os.path.join(SIG_DIR, f"backtest_{m}_{y}.csv")) as f:
                rd = iter(f); next(rd)
                for line in rd:
                    p = line.rstrip().split(",")
                    rows.append((int(p[0]), 0 if p[2] == "BUY" else 1))
            if rows:
                arr = np.array(rows)
                sigs[m] = (arr[:, 0].astype(np.int64), arr[:, 1].astype(np.int64))
        P[y] = dict(o=o, h=h, l=l, c=c, hour=hour, r1=r1, dir_bull=dir_bull,
                    atr=_atr(h, l, c), sigs=sigs,
                    ema_ok={n: (c > _ema(c, n)) for n in (50, 100, 200)})
    PREC = P
    return P

# ----------------------------------------------------------------------------
# GENOME (DNA) + operators
# ----------------------------------------------------------------------------
def random_genome(rng):
    return dict(
        mode=rng.choice(MODES),
        sl=round(rng.uniform(0.3, 6.0), 2),
        sl_atr=rng.choice([None, None, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0]),
        tp=rng.choice(["close", "rr"]),
        rr=round(rng.uniform(2.0, 10.0), 1),
        quiet=rng.choice([None, None, round(rng.uniform(0.4, 3.0), 2)]),
        sess=rng.choice([None, "london_ny", "asia", "ny", "london"]),
        c1dir=bool(rng.random() < 0.3),
        emaN=rng.choice([None, None, None, 50, 100, 200]),
        body=round(rng.choice([0.0, 0.0, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0]), 2),
        cool=rng.choice([0, 0, 0, 0, 1, 2]),
        risk=rng.choice([0.25, 0.5, 0.75, 1.0]),
        seed=rng.randint(0, 2**31),
    )

def signature(g):
    return (g["mode"], g["sl"], g["sl_atr"], g["tp"], g["rr"], g["quiet"],
            g["sess"], g["c1dir"], g["emaN"], g["body"], g["cool"], g["risk"])

def mutate(g, rng):
    ng = dict(g)
    if rng.random() < 0.08: ng["mode"] = rng.choice(MODES)
    if rng.random() < 0.3: ng["sl"] = round(max(0.3, min(6.0, ng["sl"] + rng.uniform(-0.5, 0.5))), 2)
    if rng.random() < 0.1: ng["sl_atr"] = rng.choice([None, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0])
    if rng.random() < 0.2: ng["tp"] = rng.choice(["close", "rr"])
    if rng.random() < 0.2: ng["rr"] = round(max(2.0, min(10.0, ng["rr"] + rng.uniform(-1, 1))), 1)
    if rng.random() < 0.2: ng["quiet"] = rng.choice([None, round(rng.uniform(0.4, 3.0), 2)])
    if rng.random() < 0.15: ng["sess"] = rng.choice([None, "london_ny", "asia", "ny", "london"])
    if rng.random() < 0.1: ng["c1dir"] = not ng["c1dir"]
    if rng.random() < 0.15: ng["emaN"] = rng.choice([None, 50, 100, 200])
    if rng.random() < 0.2: ng["body"] = round(rng.choice([0.0, 0.2, 0.4, 0.6, 0.8, 1.0]), 2)
    if rng.random() < 0.1: ng["cool"] = rng.choice([0, 1, 2])
    if rng.random() < 0.1: ng["risk"] = rng.choice([0.25, 0.5, 0.75, 1.0])
    ng["seed"] = rng.randint(0, 2**31)
    return ng

def crossover(a, b, rng):
    keys = ["mode", "sl", "sl_atr", "tp", "rr", "quiet", "sess", "c1dir",
            "emaN", "body", "cool", "risk"]
    ng = {}
    for k in keys:
        ng[k] = a[k] if rng.random() < 0.5 else b[k]
    ng["seed"] = rng.randint(0, 2**31)
    return ng

# ----------------------------------------------------------------------------
# EVALUATION (vectorized, conservative)
# ----------------------------------------------------------------------------
def eval_genome(g):
    P = PREC
    parts = []
    for y in YEARS:
        sig = P[y]["sigs"].get(g["mode"])
        if not sig:
            continue
        idx, side = sig
        m = np.ones(len(idx), bool)
        if g["quiet"] is not None:
            m &= P[y]["r1"][idx - 1] <= g["quiet"]
        if g["sess"]:
            m &= np.isin(P[y]["hour"][idx], SESSIONS[g["sess"]])
        if g["c1dir"]:
            m &= (P[y]["dir_bull"][idx - 1] == (side == 0))
        if g["emaN"]:
            m &= (P[y]["ema_ok"][g["emaN"]][idx - 1] == (side == 0))
        if g["body"] > 0:
            body = P[y]["c"][idx - 1] - P[y]["o"][idx - 1]
            m &= np.where(side == 0, body >= g["body"], -body >= g["body"])
        if not m.any():
            continue
        ii, ss = idx[m], side[m]
        entry = P[y]["o"][ii]
        buy = ss == 0
        if g["sl_atr"]:
            sl = np.maximum(0.3, g["sl_atr"] * P[y]["atr"][ii - 1])
        else:
            sl = np.full(len(ii), g["sl"])
        slpx = np.where(buy, entry - sl, entry + sl)
        sl_hit = np.where(buy, P[y]["l"][ii] <= slpx, P[y]["h"][ii] >= slpx)
        close_p = np.where(buy, P[y]["c"][ii] - entry, entry - P[y]["c"][ii])
        if g["tp"] == "close":
            p = np.where(sl_hit, -sl, close_p)
        else:
            tp = g["rr"] * sl
            tppx = np.where(buy, entry + tp, entry - tp)
            tp_hit = np.where(buy, P[y]["h"][ii] >= tppx, P[y]["l"][ii] <= tppx)
            both = sl_hit & tp_hit
            p = np.where(both, -sl, np.where(sl_hit, -sl, np.where(tp_hit, tp, close_p)))
        parts.append(p)
    if not parts:
        return None
    pnl = np.concatenate(parts)
    if g["cool"] > 0:
        keep = []
        skip = 0
        for p in pnl:
            if skip > 0:
                skip -= 1
                continue
            keep.append(p)
            if p < 0:
                skip = g["cool"]
        pnl = np.array(keep)
    pnl = pnl * g["risk"]
    n = len(pnl)
    if n == 0:
        return None
    wins = int((pnl > 0).sum()); losses = n - wins
    wr = 100.0 * wins / n
    net = float(pnl.sum())
    winp = pnl[pnl > 0]; lossp = pnl[pnl < 0]
    sw = float(winp.sum()); sl_ = -float(lossp.sum())
    pf = sw / sl_ if sl_ > 0 else (float("inf") if sw > 0 else 0.0)
    rr = (float(winp.mean()) / abs(float(lossp.mean()))) if len(winp) and len(lossp) else 0.0
    eq = np.cumsum(pnl)
    maxdd = float((eq - np.maximum.accumulate(eq)).min())
    benchmark = n >= BENCH_TRADES and wr >= BENCH_WR and rr >= BENCH_RR and net > 0
    return dict(trades=n, wins=wins, losses=losses, wr=wr, net=net, pf=pf,
                rr=rr, maxdd=maxdd, benchmark=benchmark, genes=g)

def score(m):
    if m is None or m["trades"] == 0 or m["net"] <= 0:
        return -1e9
    vol = min(1.0, m["trades"] / BENCH_TRADES)
    dd_pen = 1.0 / (1.0 + abs(m["maxdd"]) / 200.0)
    return m["net"] * vol * dd_pen * math.sqrt(m["pf"])

# ----------------------------------------------------------------------------
# STATE / PERSISTENCE
# ----------------------------------------------------------------------------
class State:
    def __init__(self):
        self.registry = {}          # sig -> metrics
        self.population = []        # [(sig, score)]
        self.leaderboard = {o: [] for o in ["score", "wr", "pf", "rr", "net"]}
        self.cycles = 0
        self.evals = 0
        self.unique = 0
        self.benchmark_hits = []
        self.started = time.time()
        self.last_commit = 0
        self.last_digest = 0
        self.history = []           # cycle summaries (small)
        self.lock = threading.Lock()

STATE = State()

def save_state():
    os.makedirs(EVO_DIR, exist_ok=True)
    with STATE.lock:
        top = sorted(STATE.registry.items(), key=lambda kv: -score(kv[1]))[:20000]
        with open(os.path.join(EVO_DIR, "registry_top.json"), "w") as f:
            json.dump([{ "sig": list(k), "m": v } for k, v in top], f)
        with open(os.path.join(EVO_DIR, "state.json"), "w") as f:
            json.dump(dict(cycles=STATE.cycles, evals=STATE.evals,
                           unique=STATE.unique,
                           started=STATE.started,
                           last_commit=STATE.last_commit), f)
        with open(os.path.join(EVO_DIR, "leaderboard.json"), "w") as f:
            json.dump(STATE.leaderboard, f)
        with open(os.path.join(EVO_DIR, "cycle_summary.jsonl"), "a") as f:
            if STATE.history:
                f.write(json.dumps(STATE.history[-1]) + "\n")

def load_state():
    try:
        with open(os.path.join(EVO_DIR, "state.json")) as f:
            st = json.load(f)
        STATE.cycles = st.get("cycles", 0); STATE.evals = st.get("evals", 0)
        STATE.unique = st.get("unique", 0)
        STATE.started = st.get("started", time.time())
    except Exception:
        pass
    try:
        with open(os.path.join(EVO_DIR, "registry_top.json")) as f:
            data = json.load(f)
        for item in data:
            STATE.registry[tuple(item["sig"])] = item["m"]
        STATE.population = sorted(
            ((k, score(m)) for k, m in STATE.registry.items()),
            key=lambda kv: -kv[1])[:200]
    except Exception:
        pass

def refresh_leaderboards():
    with STATE.lock:
        for o in STATE.leaderboard:
            STATE.leaderboard[o] = []
        items = [m for m in STATE.registry.values() if m["trades"] >= 500]
        by_score = sorted(items, key=score, reverse=True)
        STATE.leaderboard["score"] = by_score[:10]
        for key in ["wr", "pf", "rr", "net"]:
            STATE.leaderboard[key] = sorted(items, key=lambda m: -m[key])[:10]

# ----------------------------------------------------------------------------
# CHARTS
# ----------------------------------------------------------------------------
def make_charts():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(EVO_DIR, exist_ok=True)
    # 1) leaderboard WR/PF bars
    items = [m for m in STATE.registry.values() if m["trades"] >= 1000]
    if items:
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.5))
        top = sorted(items, key=lambda m: -m["wr"])[:8]
        a1.barh([t["genes"]["mode"] + " sl" + str(t["genes"]["sl"]) for t in top][::-1],
                [t["wr"] for t in top][::-1], color="#4da3ff")
        a1.set_title(f"Top WR (>=1000 trades) — best {top[0]['wr']:.1f}%")
        top = sorted(items, key=lambda m: -m["pf"])[:8]
        a2.barh([t["genes"]["mode"] + " sl" + str(t["genes"]["sl"]) for t in top][::-1],
                [t["pf"] for t in top][::-1], color="#6bff8f")
        a2.set_title(f"Top PF — best {top[0]['pf']:.2f}")
        plt.tight_layout()
        plt.savefig(os.path.join(EVO_DIR, "leaderboard.png"), dpi=100)
        plt.close()
    # 2) history: best score over time
    if len(STATE.history) >= 2:
        xs = [h["cycle"] for h in STATE.history]
        ys = [h["best_score"] for h in STATE.history]
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.plot(xs, ys, color="#ff9f43", lw=2)
        ax.set_title("Evolution progress — best score per cycle")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(os.path.join(EVO_DIR, "evolution.png"), dpi=100)
        plt.close()

# ----------------------------------------------------------------------------
# GITHUB AUTO-COMMIT
# ----------------------------------------------------------------------------
def git_commit():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return None
    try:
        subprocess.run("git add -A", shell=True, cwd=ROOT, capture_output=True)
        subprocess.run(f"git -c user.name='AgentEvo' -c user.email='evo@local' "
                       f"commit -m 'evolution cycle {STATE.cycles}: {STATE.unique} unique "
                       f"strategies, best WR {STATE.leaderboard['wr'][0]['wr'] if STATE.leaderboard['wr'] else 0:.1f}%'",
                       shell=True, cwd=ROOT, capture_output=True)
        out = subprocess.run(
            f"git push https://x-access-token:{token}@github.com/ryder777777/"
            f"Trading-Agent-Ecosystem.git main",
            shell=True, cwd=ROOT, capture_output=True, text=True)
        STATE.last_commit = time.time()
        return out.returncode
    except Exception as e:
        return None

# ----------------------------------------------------------------------------
# TELEGRAM
# ----------------------------------------------------------------------------
def tg_send(text):
    token = os.environ.get("TG_BOT", ""); chat = os.environ.get("TG_CHAT", "")
    if not token or not chat:
        return False
    try:
        import requests
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text}, timeout=10)
        return r.ok
    except Exception:
        return False

def digest_text():
    with STATE.lock:
        lb = STATE.leaderboard
        best_wr = lb["wr"][0] if lb["wr"] else None
        best_pf = lb["pf"][0] if lb["pf"] else None
        best_net = lb["net"][0] if lb["net"] else None
    lines = [
        "🧬 Agent Ecosystem — EVOLUTION UPDATE",
        f"⏱ uptime: {(time.time()-STATE.started)/3600:.1f}h | cycles: {STATE.cycles} | "
        f"evaluations: {STATE.evals:,} | unique: {STATE.unique:,}",
    ]
    if best_wr:
        lines.append(f"🏆 best WR: {best_wr['wr']:.1f}% ({best_wr['trades']} trades) "
                     f"[{best_wr['genes']['mode']} sl={best_wr['genes']['sl']} "
                     f"quiet={best_wr['genes']['quiet']}]")
    if best_pf:
        lines.append(f"💰 best PF: {best_pf['pf']:.2f} ({best_pf['trades']} trades)")
    if best_net:
        lines.append(f"📈 best net: ${best_net['net']:.0f} ({best_net['trades']} trades)")
    lines.append(f"🎯 benchmark (75% WR + 1:3 RR + 3000 trades): {len(STATE.benchmark_hits)} passed")
    lines.append("📦 github.com/ryder777777/Trading-Agent-Ecosystem")
    return "\n".join(lines)

# ----------------------------------------------------------------------------
# CYCLE
# ----------------------------------------------------------------------------
def one_cycle(rng, batch, objective):
    new_evaled = 0
    candidates = []
    elites = [sig for sig, _ in sorted(STATE.population, key=lambda kv: -kv[1])[:40]]
    for _ in range(batch):
        r = rng.random()
        if r < 0.15 and STATE.population:
            g = random_genome(rng)
        elif r < 0.45 and len(elites) >= 2:
            a = dict(STATE.registry[rng.choice(elites)]["genes"])
            b = dict(STATE.registry[rng.choice(elites)]["genes"])
            g = crossover(a, b, rng)
            if rng.random() < 0.6:
                g = mutate(g, rng)
        elif STATE.population:
            a = dict(STATE.registry[rng.choice(elites)]["genes"])
            g = mutate(a, rng)
        else:
            g = random_genome(rng)
        sig = signature(g)
        if sig in STATE.registry:
            continue
        m = eval_genome(g)
        if m is None:
            continue
        STATE.registry[sig] = m
        STATE.evals += 1
        new_evaled += 1
        if m["benchmark"]:
            STATE.benchmark_hits.append(m)
        candidates.append((sig, score(m)))
    STATE.unique = len(STATE.registry)
    if candidates:
        combined = STATE.population + sorted(candidates, key=lambda kv: -kv[1])[:batch]
        STATE.population = sorted(combined, key=lambda kv: -kv[1])[:200]
    refresh_leaderboards()
    best = STATE.leaderboard["score"][0] if STATE.leaderboard["score"] else None
    STATE.cycles += 1
    STATE.history.append(dict(cycle=STATE.cycles, new=new_evaled,
                              unique=STATE.unique,
                              best_score=score(best) if best else 0,
                              best_wr=STATE.leaderboard["wr"][0]["wr"]
                              if STATE.leaderboard["wr"] else 0))
    if len(STATE.history) > 5000:
        STATE.history = STATE.history[-5000:]
    return new_evaled

# ----------------------------------------------------------------------------
# STATUS SERVER (live dashboard)
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        if self.path.startswith("/status.json"):
            self._json()
        else:
            self._html()
    def _json(self):
        body = json.dumps(dict(
            uptime=time.time() - STATE.started, cycles=STATE.cycles,
            evals=STATE.evals, unique=STATE.unique,
            benchmark=len(STATE.benchmark_hits),
            leaderboard={o: [{ "wr": m["wr"], "pf": m["pf"], "net": m["net"],
                               "trades": m["trades"], "rr": m["rr"],
                               "mode": m["genes"]["mode"], "sl": m["genes"]["sl"],
                               "quiet": m["genes"]["quiet"],
                               "sess": m["genes"]["sess"] }
                            for m in STATE.leaderboard[o][:5]]
                         for o in ["score", "wr", "pf", "net", "rr"]},
            last_commit=STATE.last_commit)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
    def _html(self):
        html = _dashboard_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

def _dashboard_html():
    lb = STATE.leaderboard
    def row(m, tag=""):
        return (f"<tr><td>{m['genes']['mode']}</td><td>{m['genes']['sl']}</td>"
                f"<td>{m['genes']['sl_atr'] or '-'}</td><td>{m['genes']['quiet'] or '-'}</td>"
                f"<td>{m['genes']['sess'] or '-'}</td><td>{m['genes']['emaN'] or '-'}</td>"
                f"<td>{m['trades']}</td><td>{m['wr']:.1f}%</td><td>{m['pf']:.2f}</td>"
                f"<td>${m['net']:.0f}</td><td>{m['rr']:.2f}</td>{tag}</tr>")
    rows_wr = "".join(row(m) for m in lb["wr"][:8]) or "<tr><td colspan=11>—</td></tr>"
    rows_score = "".join(row(m) for m in lb["score"][:8]) or "<tr><td colspan=11>—</td></tr>"
    rows_pf = "".join(row(m) for m in lb["pf"][:8]) or "<tr><td colspan=11>—</td></tr>"
    up = (time.time() - STATE.started) / 3600
    next_commit = max(0, 900 - (time.time() - STATE.last_commit)) if STATE.last_commit else 0
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="10">
<title>Agent Ecosystem — Evolution Live</title>
<style>
 body{{font-family:Arial,sans-serif;background:#0e1117;color:#e6e6e6;margin:0;padding:20px}}
 h1{{font-size:22px;color:#ff9f43}} .cards{{display:flex;gap:14px;flex-wrap:wrap;margin:14px 0}}
 .card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 18px;min-width:130px}}
 .card b{{font-size:26px;display:block;color:#fff}} .card span{{font-size:12px;color:#8b949e}}
 table{{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}}
 th,td{{border:1px solid #30363d;padding:6px 8px;text-align:left}}
 th{{background:#161b22;color:#ff9f43}} tr:nth-child(even){{background:#11161d}}
 h2{{font-size:16px;color:#4da3ff;margin-top:22px}}
 .note{{color:#8b949e;font-size:12px;margin-top:20px;line-height:1.6}}
</style></head><body>
<h1>🧬 Agent Ecosystem — Evolution Engine (LIVE)</h1>
<div class="cards">
 <div class="card"><b>{up:.1f}h</b><span>uptime</span></div>
 <div class="card"><b>{STATE.cycles:,}</b><span>cycles</span></div>
 <div class="card"><b>{STATE.evals:,}</b><span>evaluations</span></div>
 <div class="card"><b>{STATE.unique:,}</b><span>unique strategies</span></div>
 <div class="card"><b>{len(STATE.benchmark_hits)}</b><span>benchmark passes</span></div>
 <div class="card"><b>{int(next_commit)}s</b><span>next GitHub commit</span></div>
</div>
<h2>🏆 Best by Win Rate (>=500 trades)</h2>
<table><tr><th>mode</th><th>SL</th><th>SL-ATR</th><th>quiet</th><th>sess</th><th>EMA</th>
<th>trades</th><th>WR</th><th>PF</th><th>net</th><th>RR</th></tr>{rows_wr}</table>
<h2>🎯 Best overall (score)</h2>
<table><tr><th>mode</th><th>SL</th><th>SL-ATR</th><th>quiet</th><th>sess</th><th>EMA</th>
<th>trades</th><th>WR</th><th>PF</th><th>net</th><th>RR</th></tr>{rows_score}</table>
<h2>💰 Best Profit Factor</h2>
<table><tr><th>mode</th><th>SL</th><th>SL-ATR</th><th>quiet</th><th>sess</th><th>EMA</th>
<th>trades</th><th>WR</th><th>PF</th><th>net</th><th>RR</th></tr>{rows_pf}</table>
<div class="note">Conservative P&L (both TP+SL touch ⇒ SL first) · candle-open entries · no repaint ·
1.06M GOLD M1 candles (2023-2026). Charts + full logs: github.com/ryder777777/Trading-Agent-Ecosystem/evolution/</div>
</body></html>"""

def status_server():
    port = int(os.environ.get("PORT", "8080"))
    srv = HTTPServer(("0.0.0.0", port), Handler)
    srv.serve_forever()

# ----------------------------------------------------------------------------
# MAIN LOOP
# ----------------------------------------------------------------------------
def main():
    precompute()
    load_state()
    os.makedirs(EVO_DIR, exist_ok=True)

    # seed population with known good strategies if registry empty
    if not STATE.registry:
        rng = random.Random(7)
        seeds = [
            dict(mode="SUPER_LOOSE", sl=1.5, sl_atr=None, tp="close", rr=4.0,
                 quiet=None, sess=None, c1dir=False, emaN=None, body=0.0,
                 cool=0, risk=1.0, seed=1),
            dict(mode="SUPER_LOOSE", sl=1.5, sl_atr=None, tp="close", rr=4.0,
                 quiet=1.0, sess=None, c1dir=False, emaN=None, body=0.0,
                 cool=0, risk=1.0, seed=2),
            dict(mode="SUPER_LOOSE", sl=0.5, sl_atr=None, tp="close", rr=4.0,
                 quiet=None, sess=None, c1dir=False, emaN=None, body=0.0,
                 cool=0, risk=1.0, seed=3),
        ]
        for g in seeds:
            m = eval_genome(g)
            if m:
                STATE.registry[signature(g)] = m
        for _ in range(300):
            g = random_genome(rng)
            if signature(g) in STATE.registry:
                continue
            m = eval_genome(g)
            if m:
                STATE.registry[signature(g)] = m
        STATE.population = sorted(
            ((k, score(m)) for k, m in STATE.registry.items()),
            key=lambda kv: -kv[1])[:200]
        refresh_leaderboards()
        print(f"[seed] registry seeded: {len(STATE.registry)}")

    threading.Thread(target=status_server, daemon=True).start()

    rng = random.Random(int(time.time()))
    batch = int(os.environ.get("BATCH", "1500"))
    cycle_delay = float(os.environ.get("CYCLE_DELAY", "8"))
    commit_every = int(os.environ.get("COMMIT_EVERY", "900"))
    digest_every = int(os.environ.get("DIGEST_EVERY", "7200"))

    # startup: immediate commit + digest
    make_charts(); save_state()
    git_commit()
    tg_send("🧬 Agent Ecosystem evolution engine STARTED.\n"
            "24h continuous backtest + self-improvement loop live.\n"
            "Dashboard + logs: github.com/ryder777777/Trading-Agent-Ecosystem")
    print("[startup] committed + notified")

    last_commit = time.time()
    last_digest = time.time()
    obj_idx = 0
    while True:
        try:
            obj = OBJ_LIST[obj_idx % len(OBJ_LIST)]
            obj_idx += 1
            new = one_cycle(rng, batch, obj)
            if new > 0 and (STATE.cycles % 10 == 0):
                make_charts(); save_state()
            if time.time() - last_commit >= commit_every:
                make_charts(); save_state(); git_commit()
                last_commit = time.time()
                print(f"[commit] cycle {STATE.cycles} evals {STATE.evals} unique {STATE.unique}")
            if time.time() - last_digest >= digest_every:
                tg_send(digest_text())
                last_digest = time.time()
                print("[digest] sent")
            if STATE.cycles % 25 == 0:
                print(f"[cycle {STATE.cycles}] evals={STATE.evals} unique={STATE.unique} "
                      f"bench={len(STATE.benchmark_hits)}")
            time.sleep(cycle_delay)
        except KeyboardInterrupt:
            make_charts(); save_state(); git_commit()
            break
        except Exception as exc:
            print(f"[error] {exc}")
            time.sleep(10)

if __name__ == "__main__":
    main()
