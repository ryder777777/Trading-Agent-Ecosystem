"""
EVOLUTION ENGINE v3 — UNLIMITED STRATEGY SPACE + SMART SELF-CODING AGENTS.

UPGRADES:
1) UNLIMITED STRATEGIES: variable-length program genome (entry + confluence +
   0..3 filters + exit). Mutation adds/removes/rewrites blocks; crossover
   splices programs -> combinatorial space.
2) SELF-CODING: top agents rendered to real Python (agents/Agent_XXXXX.py).
3) SMART EVOLUTION: tournament selection, adaptive mutation, 10,000-agent live
   population with generational replacement.
4) MULTI-OBJECTIVE FITNESS: net * sqrt(PF) * WR-boost * RR-boost * vol * dd-pen.
5) Vectorized primitives (pandas rolling) + finite param grid + LRU idx cache.

Honest: candle-open entries, no repaint, conservative P&L, benchmark
trades>=3000 & WR>=75% & RR>=3.
"""
import json
import math
import os
import random
import threading
import time
import subprocess
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pandas as pd

YEARS = ["2023", "2024", "2025"]
DATA_PATHS = {
    "2023": "/home/user/uploads/GOLD.i#_M1_2023 to 2024.csv",
    "2024": "/home/user/uploads/GOLD.i#_M1_2024 to 2025.csv",
    "2025": "/home/user/uploads/GOLD.i#_M1 2025 to 2026.csv",
}
ROOT = os.path.dirname(os.path.abspath(__file__))
EVO_DIR = os.path.join(ROOT, "evolution")
AGENT_DIR = os.path.join(ROOT, "agents")

N_POP = int(os.environ.get("N_POP", "10000"))
BATCH = int(os.environ.get("BATCH", "2500"))
TOURNAMENT_K = 3
PRIM_CACHE_MAX = int(os.environ.get("PRIM_CACHE", "200"))

BENCH_TRADES, BENCH_WR, BENCH_RR = 3000, 75.0, 3.0
BASE_MUT = 0.35

SESSIONS = {
    "london_ny": [8, 9, 10, 11, 12, 13, 14, 15, 16],
    "asia": [0, 1, 2, 3, 4, 5, 6, 7],
    "ny": [12, 13, 14, 15, 16, 17, 18, 19, 20],
    "london": [8, 9, 10, 11, 12, 13, 14, 15],
}

# ----------------------------------------------------------------------------
# DATA
# ----------------------------------------------------------------------------
DATA = None

def load_data():
    global DATA
    if DATA is not None:
        return DATA
    D = {}
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
        D[y] = dict(o=o, h=h, l=l, c=c, t=t, hour=hour)
    DATA = D
    return D

# ----------------------------------------------------------------------------
# VECTORIZED INDICATORS (pandas rolling / ewm — C-speed)
# ----------------------------------------------------------------------------
def ema(a, n):
    return pd.Series(a).ewm(span=n, adjust=False).mean().to_numpy()

def sma(a, n):
    return pd.Series(a).rolling(n, min_periods=n).mean().to_numpy()

def stdv(a, n):
    return pd.Series(a).rolling(n, min_periods=n).std().to_numpy()

def rsi(c, p=14):
    s = pd.Series(c)
    d = s.diff()
    up = d.clip(lower=0.0); dn = (-d).clip(lower=0.0)
    ru = up.ewm(alpha=1/p, adjust=False).mean()
    rd = dn.ewm(alpha=1/p, adjust=False).mean()
    rs = ru / rd.replace(0, np.nan)
    r = 100 - 100 / (1 + rs)
    return r.fillna(50).to_numpy()

def atr(h, l, c, n=14):
    pc = pd.Series(c).shift(1)
    tr = pd.concat([pd.Series(h) - pd.Series(l), (pd.Series(h) - pc).abs(),
                    (pd.Series(l) - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean().to_numpy()

def rolling_max(a, n):
    return pd.Series(a).rolling(n, min_periods=n).max().to_numpy()

def rolling_min(a, n):
    return pd.Series(a).rolling(n, min_periods=n).min().to_numpy()

def rolling_med(a, n):
    return pd.Series(a).rolling(n, min_periods=n).median().to_numpy()

def supertrend(h, l, c, p=10, m=3.0):
    a = atr(h, l, c, p)
    hl = (np.asarray(h) + np.asarray(l)) / 2
    fu = hl + m * a; fl = hl - m * a
    n = len(c); st = np.empty(n); d = np.empty(n)
    st[0] = fu[0]; d[0] = 1
    for i in range(1, n):
        fu[i] = fu[i] if (fu[i] < fu[i-1] or c[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = fl[i] if (fl[i] > fl[i-1] or c[i-1] < fl[i-1]) else fl[i-1]
        if st[i-1] == fu[i-1]:
            st[i], d[i] = (fu[i], -1) if c[i] <= fu[i] else (fl[i], 1)
        else:
            st[i], d[i] = (fl[i], 1) if c[i] >= fl[i] else (fu[i], -1)
    return d

def cross_up(a, b):
    a = np.asarray(a); b = np.asarray(b)
    up = a > b
    return up & ~np.concatenate([[False], up[:-1]])

def cross_dn(a, b):
    a = np.asarray(a); b = np.asarray(b)
    dn = a < b
    return dn & ~np.concatenate([[False], dn[:-1]])

# ----------------------------------------------------------------------------
# PARAM GRIDS (finite, so primitive cache is bounded)
# ----------------------------------------------------------------------------
GRIDS = {
    "ema_cross":  [dict(f=f, s=s) for f in (5, 9, 10, 15, 20, 30, 50)
                   for s in (21, 30, 40, 50, 100, 150, 200) if f < s],
    "price_ema":  [dict(n=n) for n in (10, 20, 50, 100, 150, 200)],
    "rsi_level":  [dict(p=p, lo=lo, hi=hi) for p in (7, 14, 21)
                   for lo in (25, 30, 35) for hi in (65, 70, 75)],
    "rsi_50":     [dict(p=p) for p in (7, 14, 21)],
    "macd":       [dict(f=8, s=21, g=5), dict(f=12, s=26, g=9),
                   dict(f=5, s=13, g=5), dict(f=12, s=26, g=5)],
    "stoch":      [dict(p=p, d=dd) for p in (5, 14, 21) for dd in (3, 5)],
    "boll_mr":    [dict(p=p, k=k) for p in (20, 50, 100) for k in (1.5, 2.0, 2.5, 3.0)],
    "boll_break": [dict(p=p, k=k) for p in (20, 50) for k in (2.0, 2.5, 3.0)],
    "donch_break":[dict(n=n) for n in (20, 30, 55, 100)],
    "donch_mr":   [dict(n=n) for n in (20, 30, 55)],
    "atr_break":  [dict(n=n, m=m) for n in (20, 55) for m in (1.0, 1.5, 2.0)],
    "roc":        [dict(n=n, thr=t) for n in (10, 20, 50, 100) for t in (0.05, 0.1, 0.2, 0.3)],
    "supertrend": [dict(p=p, m=m) for p in (7, 10, 14) for m in (2.0, 3.0, 4.0)],
    "engulfing":  [dict()],
    "pinbar":     [dict(ratio=r) for r in (2.0, 2.5, 3.0)],
    "nr7":        [dict()],
    "doji":       [dict(thr=t) for t in (0.05, 0.1, 0.15)],
    "momentum":   [dict(n=n) for n in (5, 10, 20)],
}

# ----------------------------------------------------------------------------
# PRIMITIVES: (c,h,l,o,p) -> (buy_sig, sell_sig) boolean arrays (signal bar i)
# ----------------------------------------------------------------------------
def p_ema_cross(c, h, l, o, f, s):
    ef, es = ema(c, f), ema(c, s)
    return cross_up(ef, es), cross_dn(ef, es)

def p_price_ema(c, h, l, o, n):
    e = ema(c, n)
    return cross_up(c, e), cross_dn(c, e)

def p_rsi_level(c, h, l, o, p, lo, hi):
    r = rsi(c, p)
    return cross_up(r, lo), cross_dn(r, hi)

def p_rsi_50(c, h, l, o, p):
    r = rsi(c, p)
    return cross_up(r, 50), cross_dn(r, 50)

def p_macd(c, h, l, o, f, s, g):
    line = ema(c, f) - ema(c, s)
    sig = ema(line, g)
    return cross_up(line, sig), cross_dn(line, sig)

def p_stoch(c, h, l, o, p, d):
    ll = rolling_min(l, p); hh = rolling_max(h, p)
    k = 100 * (pd.Series(c) - pd.Series(ll)) / (pd.Series(hh) - pd.Series(ll)).replace(0, np.nan)
    k = k.fillna(50)
    ks = k.rolling(d, min_periods=d).mean()
    return cross_up(k.to_numpy(), ks.to_numpy()), cross_dn(k.to_numpy(), ks.to_numpy())

def p_boll_mr(c, h, l, o, p, k):
    mid = sma(c, p); sd = stdv(c, p)
    return c < mid - k*sd, c > mid + k*sd

def p_boll_break(c, h, l, o, p, k):
    mid = sma(c, p); sd = stdv(c, p)
    return cross_up(c, mid + k*sd), cross_dn(c, mid - k*sd)

def p_donch_break(c, h, l, o, n):
    hh = pd.Series(h).rolling(n, min_periods=n).max().shift(1).to_numpy()
    ll = pd.Series(l).rolling(n, min_periods=n).min().shift(1).to_numpy()
    return cross_up(c, hh), cross_dn(c, ll)

def p_donch_mr(c, h, l, o, n):
    hh = pd.Series(h).rolling(n, min_periods=n).max().shift(1).to_numpy()
    ll = pd.Series(l).rolling(n, min_periods=n).min().shift(1).to_numpy()
    return c < ll, c > hh

def p_atr_break(c, h, l, o, n, m):
    hh = rolling_max(h, n); ll = rolling_min(l, n)
    a = atr(h, l, c)
    med = rolling_med(a, 200)
    filt = a > m * med
    return (cross_up(c, hh) & filt), (cross_dn(c, ll) & filt)

def p_roc(c, h, l, o, n, thr):
    roc = np.zeros(len(c)); roc[n:] = (c[n:] / c[:-n] - 1) * 100
    return cross_up(roc, thr), cross_dn(roc, -thr)

def p_supertrend(c, h, l, o, p, m):
    d = supertrend(h, l, c, p, m)
    return cross_up(d, 0), cross_dn(d, 0)

def p_engulfing(c, h, l, o):
    n = len(c); buy = np.zeros(n, bool); sell = np.zeros(n, bool)
    prev_red = c[:-1] < o[:-1]; cur_green = c[1:] >= o[1:]
    buy[1:] = prev_red & cur_green & (c[1:] > o[:-1]) & (o[1:] < c[:-1])
    prev_green = c[:-1] >= o[:-1]; cur_red = c[1:] < o[1:]
    sell[1:] = prev_green & cur_red & (c[1:] > o[:-1]) & (o[1:] < c[:-1])
    return buy, sell

def p_pinbar(c, h, l, o, ratio):
    body = np.abs(c - o); upper = h - np.maximum(c, o); lower = np.minimum(c, o) - l
    return ((lower >= ratio*body) & (upper <= body) & (c > o),
            (upper >= ratio*body) & (lower <= body) & (c < o))

def p_nr7(c, h, l, o):
    rng = h - l
    rmin = rolling_min(rng, 7)
    tight = rng < np.roll(rmin, 1) * 0.8
    n = len(c); buy = np.zeros(n, bool); sell = np.zeros(n, bool)
    up = np.zeros(n, bool); dn = np.zeros(n, bool)
    up[1:] = c[1:] > h[:-1]; dn[1:] = c[1:] < l[:-1]
    # signal at i when tight[i-1] and breakout at i
    buy = tight & up; sell = tight & dn
    return buy, sell

def p_doji(c, h, l, o, thr):
    n = len(c); body = np.abs(c - o); rng = np.where(h - l == 0, 1e-9, h - l)
    doji = body < thr * rng
    fall = np.zeros(n, bool); rise = np.zeros(n, bool)
    fall[1:] = c[1:] < c[:-1]; rise[1:] = c[1:] > c[:-1]
    # doji at i, price turned at i+1 -> signal at i+1
    turn_b = doji & fall & np.concatenate([np.zeros(2, bool), c[2:] > c[1:-1]])
    turn_s = doji & rise & np.concatenate([np.zeros(2, bool), c[2:] < c[1:-1]])
    buy = np.zeros(n, bool); sell = np.zeros(n, bool)
    buy[1:] = turn_b[:-1]; sell[1:] = turn_s[:-1]
    return buy, sell

def p_momentum(c, h, l, o, n):
    roc = np.zeros(len(c)); roc[n:] = (c[n:] / c[:-n] - 1) * 100
    return cross_up(roc, 0), cross_dn(roc, 0)

OPS = {
    "ema_cross":  (p_ema_cross, "ef=ema(c,{f}); es=ema(c,{s}); buy=cross_up(ef,es); sell=cross_dn(ef,es)"),
    "price_ema":  (p_price_ema, "e=ema(c,{n}); buy=cross_up(c,e); sell=cross_dn(c,e)"),
    "rsi_level":  (p_rsi_level, "r=rsi(c,{p}); buy=cross_up(r,{lo}); sell=cross_dn(r,{hi})"),
    "rsi_50":     (p_rsi_50,    "r=rsi(c,{p}); buy=cross_up(r,50); sell=cross_dn(r,50)"),
    "macd":       (p_macd,      "line=ema(c,{f})-ema(c,{s}); sig=ema(line,{g}); buy=cross_up(line,sig); sell=cross_dn(line,sig)"),
    "stoch":      (p_stoch,     "k,ks=stoch(c,h,l,{p},{d}); buy=cross_up(k,ks); sell=cross_dn(k,ks)"),
    "boll_mr":    (p_boll_mr,   "mid=sma(c,{p}); sd=std(c,{p}); buy=c<mid-{k}*sd; sell=c>mid+{k}*sd"),
    "boll_break": (p_boll_break, "mid=sma(c,{p}); sd=std(c,{p}); buy=cross_up(c,mid+{k}*sd); sell=cross_dn(c,mid-{k}*sd)"),
    "donch_break":(p_donch_break, "hh,ll=donch(h,l,{n}); buy=cross_up(c,hh); sell=cross_dn(c,ll)"),
    "donch_mr":   (p_donch_mr,  "hh,ll=donch(h,l,{n}); buy=c<ll; sell=c>hh"),
    "atr_break":  (p_atr_break, "hh,ll=donch(h,l,{n}); a=atr(h,l,c); f=a>{m}*median(a,200); buy=cross_up(c,hh)&f; sell=cross_dn(c,ll)&f"),
    "roc":        (p_roc,       "rc=roc(c,{n}); buy=cross_up(rc,{thr}); sell=cross_dn(rc,-{thr})"),
    "supertrend": (p_supertrend, "d=st(h,l,c,{p},{m}); buy=cross_up(d,0); sell=cross_dn(d,0)"),
    "engulfing":  (p_engulfing, "buy,sell=engulfing(c,o)"),
    "pinbar":     (p_pinbar,    "buy,sell=pinbar(c,h,l,o,{ratio})"),
    "nr7":        (p_nr7,       "buy,sell=nr7(h,l,c)"),
    "doji":       (p_doji,      "buy,sell=doji_rev(c,h,l,o,{thr})"),
    "momentum":   (p_momentum,  "rc=roc(c,{n}); buy=cross_up(rc,0); sell=cross_dn(rc,0)"),
}
OP_NAMES = list(OPS.keys())
FILTER_OPS = ["quiet", "sess", "ema_align", "body", "c1dir"]

# ----------------------------------------------------------------------------
# GENOME
# ----------------------------------------------------------------------------
def random_program(rng):
    op = rng.choice(OP_NAMES)
    params = rng.choice(GRIDS[op])
    prog = {"entry": {"op": op, "p": params}}
    if rng.random() < 0.35:
        op2 = rng.choice(OP_NAMES)
        prog["conf"] = {"op": op2, "p": rng.choice(GRIDS[op2])}
    else:
        prog["conf"] = None
    prog["filters"] = []
    for fop in ["quiet", "body", "ema_align", "c1dir"]:
        if rng.random() < 0.25:
            if fop == "quiet":
                prog["filters"].append({"op": "quiet", "p": round(rng.uniform(0.5, 3.0), 2)})
            elif fop == "body":
                prog["filters"].append({"op": "body", "p": round(rng.choice([0.2, 0.4, 0.6, 0.8]), 2)})
            elif fop == "ema_align":
                prog["filters"].append({"op": "ema_align", "p": rng.choice([50, 100, 200])})
            elif fop == "c1dir":
                prog["filters"].append({"op": "c1dir", "p": None})
    if rng.random() < 0.15:
        prog["filters"].append({"op": "sess", "p": rng.choice(list(SESSIONS.keys()))})
    prog["exit"] = {
        "sl_mode": rng.choice(["fixed", "atr"]),
        "sl": round(rng.uniform(0.3, 5.0), 2),
        "sl_atr": rng.choice([0.75, 1.0, 1.5, 2.0, 2.5, 3.0]),
        "tp_mode": rng.choice(["close", "rr", "trail"]),
        "rr": round(rng.uniform(2.0, 10.0), 1),
        "trail": rng.choice([1.0, 1.5, 2.0, 3.0]),
    }
    prog["risk"] = rng.choice([0.25, 0.5, 0.75, 1.0])
    prog["cool"] = rng.choice([0, 0, 0, 0, 1, 2])
    prog["seed"] = rng.randint(0, 2**31)
    return prog

def signature(prog):
    return json.dumps(prog, sort_keys=True, separators=(",", ":"))

def mutate_program(prog, rng, rate=BASE_MUT):
    g = json.loads(json.dumps(prog))
    if rng.random() < rate * 1.2:
        newop = rng.choice(OP_NAMES)
        g["entry"] = {"op": newop, "p": rng.choice(GRIDS[newop])}
    elif rng.random() < rate:
        g["entry"]["p"] = rng.choice(GRIDS[g["entry"]["op"]])
    if rng.random() < rate * 0.5:
        if g["conf"] is None:
            op = rng.choice(OP_NAMES)
            g["conf"] = {"op": op, "p": rng.choice(GRIDS[op])}
        elif rng.random() < 0.5:
            op = rng.choice(OP_NAMES)
            g["conf"] = {"op": op, "p": rng.choice(GRIDS[op])}
        else:
            g["conf"] = None
    if rng.random() < rate:
        if g["filters"] and rng.random() < 0.5:
            g["filters"].pop(rng.randrange(len(g["filters"])))
        else:
            fop = rng.choice(FILTER_OPS)
            if fop == "quiet": g["filters"].append({"op": "quiet", "p": round(rng.uniform(0.5, 3.0), 2)})
            elif fop == "body": g["filters"].append({"op": "body", "p": round(rng.choice([0.2, 0.4, 0.6, 0.8]), 2)})
            elif fop == "ema_align": g["filters"].append({"op": "ema_align", "p": rng.choice([50, 100, 200])})
            elif fop == "c1dir": g["filters"].append({"op": "c1dir", "p": None})
            else: g["filters"].append({"op": "sess", "p": rng.choice(list(SESSIONS.keys()))})
    for f in g["filters"]:
        if rng.random() < rate * 0.6:
            if f["op"] == "quiet": f["p"] = round(rng.uniform(0.5, 3.0), 2)
            elif f["op"] == "body": f["p"] = round(rng.choice([0.2, 0.4, 0.6, 0.8]), 2)
            elif f["op"] == "ema_align": f["p"] = rng.choice([50, 100, 200])
    if rng.random() < rate:
        g["exit"]["sl_mode"] = rng.choice(["fixed", "atr"])
    if rng.random() < rate:
        g["exit"]["tp_mode"] = rng.choice(["close", "rr", "trail"])
    if rng.random() < rate:
        g["exit"]["sl"] = round(max(0.3, min(6.0, g["exit"]["sl"] + rng.uniform(-0.6, 0.6))), 2)
    if rng.random() < rate:
        g["exit"]["rr"] = round(max(2.0, min(12.0, g["exit"]["rr"] + rng.uniform(-1.5, 1.5))), 1)
    if rng.random() < rate * 0.5:
        g["risk"] = rng.choice([0.25, 0.5, 0.75, 1.0])
    if rng.random() < rate * 0.5:
        g["cool"] = rng.choice([0, 1, 2])
    g["seed"] = rng.randint(0, 2**31)
    return g

def crossover_programs(a, b, rng):
    g = json.loads(json.dumps(a))
    if rng.random() < 0.5:
        g["entry"] = json.loads(json.dumps(b["entry"]))
    if rng.random() < 0.5:
        g["conf"] = json.loads(json.dumps(b.get("conf")))
    if rng.random() < 0.5:
        g["filters"] = json.loads(json.dumps(b.get("filters", [])))
    if rng.random() < 0.5:
        g["exit"] = json.loads(json.dumps(b.get("exit", g["exit"])))
    if rng.random() < 0.3:
        g["risk"] = b.get("risk", g["risk"])
    if rng.random() < 0.3:
        g["cool"] = b.get("cool", g["cool"])
    g["seed"] = rng.randint(0, 2**31)
    return g

# ----------------------------------------------------------------------------
# PRIMITIVE CACHE (idx arrays) + EVALUATION
# ----------------------------------------------------------------------------
class LRU(OrderedDict):
    def __init__(self, maxsize):
        super().__init__(); self.maxsize = maxsize
    def get(self, k, d=None):
        if k in self:
            self.move_to_end(k); return super().get(k, d)
        return d
    def put(self, k, v):
        self[k] = v; self.move_to_end(k)
        if len(self) > self.maxsize:
            self.popitem(last=False)

PRIM_CACHE = {y: LRU(max(PRIM_CACHE_MAX, 300)) for y in YEARS}
ATR_CACHE = {}
EMA_CACHE = {}

def prewarm_primitives():
    """Compute the ENTIRE param grid once at startup -> no cache thrash later."""
    t0 = time.time()
    n = 0
    for y in YEARS:
        for op in OP_NAMES:
            for p in GRIDS[op]:
                _get_prim_idx(y, op, p)
                n += 1
    print(f"[prewarm] {n} primitive sets cached in {time.time()-t0:.0f}s")

def _atr_arr(y):
    if y not in ATR_CACHE:
        d = DATA[y]
        ATR_CACHE[y] = atr(d["h"], d["l"], d["c"])
    return ATR_CACHE[y]

def _ema_arr(y, n):
    key = (y, n)
    if key not in EMA_CACHE:
        EMA_CACHE[key] = ema(DATA[y]["c"], n)
    return EMA_CACHE[key]

def _get_prim_idx(y, op, p):
    key = (op, json.dumps(p, sort_keys=True))
    cache = PRIM_CACHE[y]
    v = cache.get(key)
    if v is not None:
        return v
    d = DATA[y]
    fn = OPS[op][0]
    buy, sell = fn(d["c"], d["h"], d["l"], d["o"], **p)
    cache.put(key, (np.where(buy)[0], np.where(sell)[0]))
    return cache[key]

def eval_genome(g):
    parts = []
    for y in YEARS:
        d = DATA[y]
        bi, si = _get_prim_idx(y, g["entry"]["op"], g["entry"]["p"])
        if g.get("conf"):
            b2, s2 = _get_prim_idx(y, g["conf"]["op"], g["conf"]["p"])
            bmask = np.zeros(len(d["c"]), bool); bmask[bi] = True
            bmask2 = np.zeros(len(d["c"]), bool); bmask2[b2] = True
            smask = np.zeros(len(d["c"]), bool); smask[si] = True
            smask2 = np.zeros(len(d["c"]), bool); smask2[s2] = True
            bidx = np.where(bmask & bmask2)[0]
            sidx = np.where(smask & smask2)[0]
        else:
            bidx, sidx = bi, si
        # entry at next open
        bidx = bidx + 1; sidx = sidx + 1
        n = len(d["c"])
        bidx = bidx[bidx < n]; sidx = sidx[sidx < n]
        if len(bidx) == 0 and len(sidx) == 0:
            continue
        idx = np.concatenate([bidx, sidx])
        side = np.concatenate([np.zeros(len(bidx), np.int64), np.ones(len(sidx), np.int64)])
        order = np.argsort(idx)
        idx, side = idx[order], side[order]
        m = np.ones(len(idx), bool)
        for f in g.get("filters", []):
            op = f["op"]; p = f["p"]
            if op == "quiet":
                m &= d["h"][idx-1] - d["l"][idx-1] <= p
            elif op == "body":
                body = d["c"][idx-1] - d["o"][idx-1]
                m &= np.where(side == 0, body >= p, -body >= p)
            elif op == "ema_align":
                e = _ema_arr(y, p)
                m &= np.where(side == 0, d["c"][idx-1] > e[idx-1], d["c"][idx-1] < e[idx-1])
            elif op == "c1dir":
                m &= np.where(side == 0, d["c"][idx-1] > d["o"][idx-1], d["c"][idx-1] < d["o"][idx-1])
            elif op == "sess":
                m &= np.isin(d["hour"][idx], SESSIONS[p])
        if not m.any():
            continue
        ii, ss = idx[m], side[m]
        entry = d["o"][ii]; buy = ss == 0
        ex = g["exit"]
        if ex["sl_mode"] == "atr":
            sl = np.maximum(0.3, ex["sl_atr"] * _atr_arr(y)[ii-1])
        else:
            sl = np.full(len(ii), ex["sl"])
        slpx = np.where(buy, entry - sl, entry + sl)
        sl_hit = np.where(buy, d["l"][ii] <= slpx, d["h"][ii] >= slpx)
        close_p = np.where(buy, d["c"][ii] - entry, entry - d["c"][ii])
        if ex["tp_mode"] == "close":
            p = np.where(sl_hit, -sl, close_p)
        elif ex["tp_mode"] == "rr":
            tp = ex["rr"] * sl
            tppx = np.where(buy, entry + tp, entry - tp)
            tp_hit = np.where(buy, d["h"][ii] >= tppx, d["l"][ii] <= tppx)
            both = sl_hit & tp_hit
            p = np.where(both, -sl, np.where(sl_hit, -sl, np.where(tp_hit, tp, close_p)))
        else:
            trail = ex["trail"] * _atr_arr(y)[ii]
            tppx = np.where(buy, entry + trail, entry - trail)
            tp_hit = np.where(buy, d["h"][ii] >= tppx, d["l"][ii] <= tppx)
            both = sl_hit & tp_hit
            p = np.where(both, -sl, np.where(sl_hit, -sl, np.where(tp_hit, trail, close_p)))
        parts.append(p)
    if not parts:
        return None
    pnl = np.concatenate(parts)
    if g.get("cool", 0) > 0:
        keep = []; skip = 0
        for p in pnl:
            if skip > 0:
                skip -= 1; continue
            keep.append(p)
            if p < 0:
                skip = g["cool"]
        pnl = np.array(keep)
    pnl = pnl * g.get("risk", 1.0)
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

def fitness(m):
    if m is None or m["trades"] < 50 or m["net"] <= 0:
        return -1e9
    vol = min(1.0, m["trades"] / BENCH_TRADES)
    dd_pen = 1.0 / (1.0 + abs(m["maxdd"]) / 300.0)
    wr_boost = (max(m["wr"], 0.1) / 50.0) ** 2.5
    rr_boost = 1.0 + min(m["rr"], 10.0) / 5.0
    return m["net"] * math.sqrt(max(m["pf"], 0.01)) * wr_boost * rr_boost * vol * dd_pen

# ----------------------------------------------------------------------------
# STATE / POPULATION
# ----------------------------------------------------------------------------
class State:
    def __init__(self):
        self.registry = {}
        self.leaderboard = {o: [] for o in ["score", "wr", "rr", "pf", "net"]}
        self.cycles = 0
        self.evals = 0
        self.unique = 0
        self.benchmark_hits = []
        self.started = time.time()
        self.last_commit = 0
        self.history = []
        self.mut_rate = BASE_MUT
        self.best_fitness = -1e9
        self.no_improve = 0
        self.lock = threading.Lock()

STATE = State()
POP = []

def save_state():
    os.makedirs(EVO_DIR, exist_ok=True)
    with STATE.lock:
        top = sorted(STATE.registry.items(), key=lambda kv: -fitness(kv[1]))[:30000]
        with open(os.path.join(EVO_DIR, "registry_top.json"), "w") as f:
            json.dump([{"sig": k, "m": v} for k, v in top], f)
        with open(os.path.join(EVO_DIR, "state.json"), "w") as f:
            json.dump(dict(cycles=STATE.cycles, evals=STATE.evals,
                           unique=STATE.unique, started=STATE.started,
                           last_commit=STATE.last_commit,
                           mut_rate=STATE.mut_rate,
                           best_fitness=STATE.best_fitness,
                           no_improve=STATE.no_improve), f)
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
        STATE.mut_rate = st.get("mut_rate", BASE_MUT)
        STATE.best_fitness = st.get("best_fitness", -1e9)
        STATE.no_improve = st.get("no_improve", 0)
    except Exception:
        pass
    try:
        with open(os.path.join(EVO_DIR, "registry_top.json")) as f:
            data = json.load(f)
        for item in data:
            try:
                STATE.registry[item["sig"]] = item["m"]
            except Exception:
                continue
    except Exception:
        pass

def refresh_leaderboards():
    with STATE.lock:
        for o in STATE.leaderboard:
            STATE.leaderboard[o] = []
        items = [m for m in STATE.registry.values() if m["trades"] >= 500]
        by_score = sorted(items, key=fitness, reverse=True)
        STATE.leaderboard["score"] = by_score[:10]
        for key in ["wr", "rr", "pf", "net"]:
            STATE.leaderboard[key] = sorted(items, key=lambda m: -m[key])[:10]

# ----------------------------------------------------------------------------
# SELF-CODING: render agent programs to real Python files
# ----------------------------------------------------------------------------
def render_agent_code(genome, name):
    g = genome
    ex = g["exit"]
    op = g["entry"]["op"]; p = g["entry"]["p"]
    if op not in OPS or not isinstance(p, dict):
        return f'"""Agent {name} — entry op {op!r} not renderable (skipped)."""\n'
    tpl = OPS[op][1]
    code_line = "    " + tpl.format(**p).replace("; ", "\n    ").replace(";", "\n    ")
    conf_line = ""
    if g.get("conf"):
        c2 = g["conf"]
        conf_line = "    " + OPS[c2["op"]][1].format(**c2["p"]).replace("; ", "\n    ").replace(";", "\n    ")
        conf_line = conf_line + "\n    buy = buy & buy2; sell = sell & sell2"
    lines = [
        f'"""Agent {name} — strategy code auto-developed by evolution engine v3.',
        "Candle-open entry: signal on closed candle i -> entry at i+1 open.",
        'No repaint, conservative P&L. Data: GOLD M1 2023-2026."""',
        "import numpy as np",
        "import pandas as pd",
        "",
        "def ema(a,n): return pd.Series(a).ewm(span=n,adjust=False).mean().to_numpy()",
        "def sma(a,n): return pd.Series(a).rolling(n,min_periods=n).mean().to_numpy()",
        "def std(a,n): return pd.Series(a).rolling(n,min_periods=n).std().to_numpy()",
        "def rsi(c,p=14):",
        "    s=pd.Series(c); d=s.diff(); up=d.clip(lower=0); dn=(-d).clip(lower=0)",
        "    ru=up.ewm(alpha=1/p,adjust=False).mean(); rd=dn.ewm(alpha=1/p,adjust=False).mean()",
        "    rs=ru/rd.replace(0,np.nan); return (100-100/(1+rs)).fillna(50).to_numpy()",
        "def atr(h,l,c,n=14):",
        "    pc=pd.Series(c).shift(1)",
        "    tr=pd.concat([pd.Series(h)-pd.Series(l),(pd.Series(h)-pc).abs(),(pd.Series(l)-pc).abs()],axis=1).max(axis=1)",
        "    return tr.ewm(alpha=1/n,adjust=False).mean().to_numpy()",
        "def donch(h,l,n): return pd.Series(h).rolling(n,min_periods=n).max().to_numpy(), pd.Series(l).rolling(n,min_periods=n).min().to_numpy()",
        "def st(h,l,c,p,m):",
        "    a=atr(h,l,c,p); hl=(np.asarray(h)+np.asarray(l))/2; fu=hl+m*a; fl=hl-m*a; n=len(c); d=np.empty(n); d[0]=1",
        "    for i in range(1,n):",
        "        fu[i]=fu[i] if (fu[i]<fu[i-1] or c[i-1]>fu[i-1]) else fu[i-1]",
        "        fl[i]=fl[i] if (fl[i]>fl[i-1] or c[i-1]<fl[i-1]) else fl[i-1]",
        "        d[i]=-1 if c[i]<=fu[i] else 1 if c[i]>=fl[i] else d[i-1]",
        "    return d",
        "def stoch(c,h,l,p,d):",
        "    ll=pd.Series(l).rolling(p,min_periods=p).min(); hh=pd.Series(h).rolling(p,min_periods=p).max()",
        "    k=100*(pd.Series(c)-ll)/(hh-ll).replace(0,np.nan); k=k.fillna(50); return k.to_numpy(), k.rolling(d,min_periods=d).mean().to_numpy()",
        "def roc(c,n): r=np.zeros(len(c)); r[n:]=(c[n:]/c[:-n]-1)*100; return r",
        "def cross_up(a,b): a=np.asarray(a); b=np.asarray(b); up=a>b; return up&~np.concatenate([[False],up[:-1]])",
        "def cross_dn(a,b): a=np.asarray(a); b=np.asarray(b); dn=a<b; return dn&~np.concatenate([[False],dn[:-1]])",
        "def engulfing(c,o):",
        "    n=len(c); buy=np.zeros(n,bool); sell=np.zeros(n,bool)",
        "    buy[1:]=(c[:-1]<o[:-1])&(c[1:]>=o[1:])&(c[1:]>o[:-1])&(o[1:]<c[:-1])",
        "    sell[1:]=(c[:-1]>=o[:-1])&(c[1:]<o[1:])&(c[1:]>o[:-1])&(o[1:]<c[:-1])",
        "    return buy,sell",
        "def pinbar(c,h,l,o,ratio):",
        "    body=np.abs(c-o); up=h-np.maximum(c,o); lo=np.minimum(c,o)-l",
        "    return (lo>=ratio*body)&(up<=body)&(c>o), (up>=ratio*body)&(lo<=body)&(c<o)",
        "def nr7(h,l,c):",
        "    rng=h-l; rmin=pd.Series(rng).rolling(7,min_periods=7).min().to_numpy(); tight=rng<np.roll(rmin,1)*0.8",
        "    up=np.zeros(len(c),bool); dn=np.zeros(len(c),bool); up[1:]=c[1:]>h[:-1]; dn[1:]=c[1:]<l[:-1]",
        "    return tight&up, tight&dn",
        "def doji_rev(c,h,l,o,thr):",
        "    n=len(c); body=np.abs(c-o); rng=np.where(h-l==0,1e-9,h-l); doji=body<thr*rng",
        "    tb=doji&np.concatenate([[False],c[1:]<c[:-1]])&np.concatenate([np.zeros(2,bool),c[2:]>c[1:-1]])",
        "    ts=doji&np.concatenate([[False],c[1:]>c[:-1]])&np.concatenate([np.zeros(2,bool),c[2:]<c[1:-1]])",
        "    buy=np.zeros(n,bool); sell=np.zeros(n,bool); buy[1:]=tb[:-1]; sell[1:]=ts[:-1]; return buy,sell",
        "",
        "def entry_signals(c, h, l, o):",
        f"    # ENTRY BLOCK: {op} {p}",
        code_line,
        conf_line,
        "    return buy, sell",
        "",
        f"EXIT = {json.dumps(ex)}",
        f"FILTERS = {json.dumps(g.get('filters', []))}",
        f"RISK = {g.get('risk', 1.0)}",
        f"COOL = {g.get('cool', 0)}",
    ]
    return "\n".join(lines)

def write_agent_files(top_n=10):
    os.makedirs(AGENT_DIR, exist_ok=True)
    items = STATE.leaderboard["score"][:top_n]
    idx_line = []
    for i, m in enumerate(items):
        try:
            name = m["genes"].get("_name", f"Agent_top{i:02d}")
            code = render_agent_code(m["genes"], name)
            safe = name.replace(" ", "_").replace("/", "_")
            with open(os.path.join(AGENT_DIR, f"{safe}.py"), "w") as f:
                f.write(code)
            g = m["genes"]
            idx_line.append(f"- `{name}`: entry={g['entry']['op']} "
                            f"conf={(g.get('conf') or {}).get('op', '-')} "
                            f"filters={[x['op'] for x in g.get('filters', [])]} "
                            f"exit={g['exit']} | WR {m['wr']:.1f}% RR {m['rr']:.2f} net ${m['net']:.0f}")
        except Exception as exc:
            print(f"[render-skip] {m.get('_name', '?')}: {exc}")
    with open(os.path.join(AGENT_DIR, "INDEX.md"), "w") as f:
        f.write("# Agent Codebase — auto-developed by evolution v3\n\n" + "\n".join(idx_line) + "\n")

# ----------------------------------------------------------------------------
# CHARTS
# ----------------------------------------------------------------------------
def make_charts():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(EVO_DIR, exist_ok=True)
    items = [m for m in STATE.registry.values() if m["trades"] >= 500]
    if items:
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.5))
        top = sorted(items, key=lambda m: -m["wr"])[:8]
        a1.barh([t["genes"]["entry"]["op"] for t in top][::-1],
                [t["wr"] for t in top][::-1], color="#4da3ff")
        a1.set_title(f"Top WR — best {top[0]['wr']:.1f}%")
        top = sorted(items, key=lambda m: -m["rr"])[:8]
        a2.barh([t["genes"]["entry"]["op"] for t in top][::-1],
                [t["rr"] for t in top][::-1], color="#ff9f43")
        a2.set_title(f"Top RR — best {top[0]['rr']:.2f}")
        plt.tight_layout()
        plt.savefig(os.path.join(EVO_DIR, "leaderboard.png"), dpi=100)
        plt.close()
    if len(STATE.history) >= 2:
        xs = [h["cycle"] for h in STATE.history]
        ys = [h["best_score"] for h in STATE.history]
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.plot(xs, ys, color="#6bff8f", lw=2)
        ax.set_title("Evolution progress — best fitness per cycle")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(os.path.join(EVO_DIR, "evolution.png"), dpi=100)
        plt.close()

# ----------------------------------------------------------------------------
# GIT / TG
# ----------------------------------------------------------------------------
def git_commit():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return None
    try:
        subprocess.run("git add -A", shell=True, cwd=ROOT, capture_output=True)
        subprocess.run(f"git -c user.name='AgentEvo' -c user.email='evo@local' "
                       f"commit -m 'evolution v3 cycle {STATE.cycles}: {STATE.unique} "
                       f"unique programs, pop {len(POP)} agents, best fitness {STATE.best_fitness:.0f}'",
                       shell=True, cwd=ROOT, capture_output=True)
        return subprocess.run(
            f"git push https://x-access-token:{token}@github.com/ryder777777/"
            f"Trading-Agent-Ecosystem.git main",
            shell=True, cwd=ROOT, capture_output=True, text=True).returncode
    except Exception:
        return None

def tg_send(text):
    token = os.environ.get("TG_BOT", ""); chat = os.environ.get("TG_CHAT", "")
    if not token or not chat:
        return False
    try:
        import requests
        return requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                             json={"chat_id": chat, "text": text}, timeout=10).ok
    except Exception:
        return False

def digest_text():
    with STATE.lock:
        lb = STATE.leaderboard
        bw = lb["wr"][0] if lb["wr"] else None
        br = lb["rr"][0] if lb["rr"] else None
        bp = lb["pf"][0] if lb["pf"] else None
        bn = lb["net"][0] if lb["net"] else None
    lines = [
        "🧬 Agent Ecosystem v3 — UNLIMITED STRATEGIES + SMART EVOLUTION",
        f"⏱ uptime {(time.time()-STATE.started)/3600:.1f}h | cycles {STATE.cycles} | "
        f"evals {STATE.evals:,} | unique {STATE.unique:,}",
        f"👥 active population: {len(POP):,} agents | mutation rate: {STATE.mut_rate:.2f}",
    ]
    if bw:
        lines.append(f"🏆 best WR: {bw['wr']:.1f}% ({bw['trades']} trades) [{bw['genes']['entry']['op']}]")
    if br:
        lines.append(f"📏 best RR: {br['rr']:.2f} ({br['trades']} trades) [{br['genes']['entry']['op']}]")
    if bp:
        lines.append(f"💰 best PF: {bp['pf']:.2f} ({bp['trades']} trades)")
    if bn:
        lines.append(f"📈 best net: ${bn['net']:.0f} ({bn['trades']} trades)")
    lines.append(f"🎯 benchmark (WR≥75% + RR≥3 + ≥3000): {len(STATE.benchmark_hits)} passed")
    lines.append("📦 github.com/ryder777777/Trading-Agent-Ecosystem")
    return "\n".join(lines)

# ----------------------------------------------------------------------------
# EVOLUTION
# ----------------------------------------------------------------------------
def init_population(rng):
    global POP
    seeds = []
    for op in OP_NAMES:
        g = {"entry": {"op": op, "p": GRIDS[op][0]}, "conf": None, "filters": [],
             "exit": {"sl_mode": "fixed", "sl": 1.5, "sl_atr": 1.5,
                      "tp_mode": "close", "rr": 4.0, "trail": 2.0},
             "risk": 1.0, "cool": 0, "seed": rng.randint(0, 2**31)}
        seeds.append(g)
    POP = []
    seen = set()
    t0 = time.time()
    for g in seeds:
        if len(POP) >= N_POP:
            break
        sig = signature(g)
        if sig in seen:
            continue
        m = eval_genome(g)
        if m is None:
            continue
        m["genes"]["_name"] = f"Agent_{len(POP):05d}"
        seen.add(sig)
        STATE.registry[sig] = m
        POP.append((sig, g, fitness(m)))
    while len(POP) < N_POP:
        g = random_program(rng)
        sig = signature(g)
        if sig in seen:
            continue
        m = eval_genome(g)
        if m is None:
            continue
        m["genes"]["_name"] = f"Agent_{len(POP):05d}"
        seen.add(sig)
        STATE.registry[sig] = m
        POP.append((sig, g, fitness(m)))
        if len(POP) % 2000 == 0:
            print(f"[init] {len(POP)}/{N_POP} ({time.time()-t0:.0f}s)", flush=True)
    STATE.unique = len(STATE.registry)
    refresh_leaderboards()
    print(f"[init] population {len(POP)} | registry {STATE.unique} | {time.time()-t0:.0f}s")

def one_cycle(rng):
    global POP
    offspring = []
    n_new = 0
    for _ in range(BATCH):
        a = max(random.sample(POP, TOURNAMENT_K), key=lambda x: x[2])
        b = max(random.sample(POP, TOURNAMENT_K), key=lambda x: x[2])
        g = crossover_programs(a[1], b[1], rng)
        if rng.random() < STATE.mut_rate:
            g = mutate_program(g, rng, STATE.mut_rate)
        sig = signature(g)
        if sig in STATE.registry:
            continue
        m = eval_genome(g)
        if m is None:
            continue
        STATE.evals += 1
        n_new += 1
        m["genes"]["_name"] = f"Agent_{len(STATE.registry):05d}"
        STATE.registry[sig] = m
        f = fitness(m)
        offspring.append((sig, g, f))
        if m["benchmark"]:
            STATE.benchmark_hits.append(m)
    if offspring:
        offspring.sort(key=lambda x: -x[2])
        worst = sorted(POP, key=lambda x: x[2])[:len(offspring)]
        wi = 0
        for sig, g, f in offspring:
            if f > worst[wi][2]:
                for j in range(len(POP)):
                    if POP[j][0] == worst[wi][0]:
                        POP[j] = (sig, g, f)
                        break
                wi += 1
                if wi >= len(worst):
                    break
    STATE.unique = len(STATE.registry)
    refresh_leaderboards()
    best = STATE.leaderboard["score"][0] if STATE.leaderboard["score"] else None
    bf = fitness(best) if best else -1e9
    if bf > STATE.best_fitness:
        STATE.best_fitness = bf
        STATE.no_improve = 0
        STATE.mut_rate = max(0.12, STATE.mut_rate * 0.9)
    else:
        STATE.no_improve += 1
        if STATE.no_improve > 250:
            STATE.mut_rate = min(1.0, STATE.mut_rate * 1.25)
            STATE.no_improve = 0
    STATE.cycles += 1
    STATE.history.append(dict(cycle=STATE.cycles, new=n_new, unique=STATE.unique,
                              best_score=bf if bf > -1e8 else 0,
                              best_wr=STATE.leaderboard["wr"][0]["wr"] if STATE.leaderboard["wr"] else 0,
                              best_rr=STATE.leaderboard["rr"][0]["rr"] if STATE.leaderboard["rr"] else 0,
                              mut_rate=round(STATE.mut_rate, 3)))
    if len(STATE.history) > 5000:
        STATE.history = STATE.history[-5000:]
    return n_new

# ----------------------------------------------------------------------------
# DASHBOARD
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        if self.path.startswith("/status.json"):
            body = json.dumps(dict(
                uptime=time.time() - STATE.started, cycles=STATE.cycles,
                evals=STATE.evals, unique=STATE.unique,
                benchmark=len(STATE.benchmark_hits),
                population=len(POP), mut_rate=STATE.mut_rate,
                best_fitness=STATE.best_fitness,
                n_ops=len(OP_NAMES),
                leaderboard={o: [{"strat": m["genes"]["entry"]["op"],
                                  "wr": m["wr"], "pf": m["pf"], "net": m["net"],
                                  "trades": m["trades"], "rr": m["rr"],
                                  "exit": m["genes"]["exit"],
                                  "filters": [x["op"] for x in m["genes"].get("filters", [])]}
                                 for m in STATE.leaderboard[o][:5]]
                             for o in ["score", "wr", "rr", "pf", "net"]},
                last_commit=STATE.last_commit)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            html = _dashboard_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())

def _dashboard_html():
    lb = STATE.leaderboard
    def row(m):
        g = m["genes"]
        conf = (g.get("conf") or {}).get("op", "-")
        flt = ",".join(x["op"] for x in g.get("filters", [])) or "-"
        ex = g["exit"]
        return (f"<tr><td>{g.get('_name','?')}</td><td>{g['entry']['op']}</td>"
                f"<td>{conf}</td><td>{flt}</td>"
                f"<td>{ex['sl_mode']}/{ex['sl']}</td>"
                f"<td>{ex['tp_mode']}/{ex['rr']}</td>"
                f"<td>{m['trades']}</td><td>{m['wr']:.1f}%</td>"
                f"<td>{m['pf']:.2f}</td><td>${m['net']:.0f}</td>"
                f"<td>{m['rr']:.2f}</td><td>{m['maxdd']:.1f}</td></tr>")
    rows_wr = "".join(row(m) for m in lb["wr"][:8]) or "<tr><td colspan=12>—</td></tr>"
    rows_score = "".join(row(m) for m in lb["score"][:8]) or "<tr><td colspan=12>—</td></tr>"
    rows_rr = "".join(row(m) for m in lb["rr"][:8]) or "<tr><td colspan=12>—</td></tr>"
    up = (time.time() - STATE.started) / 3600
    nxt = max(0, 900 - (time.time() - STATE.last_commit)) if STATE.last_commit else 0
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="10"><title>Agent Ecosystem v3</title>
<style>
 body{{font-family:Arial,sans-serif;background:#0e1117;color:#e6e6e6;margin:0;padding:20px}}
 h1{{font-size:22px;color:#6bff8f}} .cards{{display:flex;gap:14px;flex-wrap:wrap;margin:14px 0}}
 .card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 18px;min-width:130px}}
 .card b{{font-size:24px;display:block;color:#fff}} .card span{{font-size:12px;color:#8b949e}}
 table{{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0}}
 th,td{{border:1px solid #30363d;padding:5px 7px;text-align:left}}
 th{{background:#161b22;color:#6bff8f}} tr:nth-child(even){{background:#11161d}}
 h2{{font-size:16px;color:#4da3ff;margin-top:22px}}
 .note{{color:#8b949e;font-size:12px;margin-top:20px;line-height:1.6}}
</style></head><body>
<h1>🧬 Agent Ecosystem v3 — UNLIMITED STRATEGIES · SMART EVOLUTION (LIVE)</h1>
<div class="cards">
 <div class="card"><b>{up:.1f}h</b><span>uptime</span></div>
 <div class="card"><b>{STATE.cycles:,}</b><span>generations</span></div>
 <div class="card"><b>{STATE.evals:,}</b><span>evaluations</span></div>
 <div class="card"><b>{STATE.unique:,}</b><span>unique programs</span></div>
 <div class="card"><b>{len(POP):,}</b><span>active agents</span></div>
 <div class="card"><b>{len(OP_NAMES)}</b><span>primitives (unlimited combos)</span></div>
 <div class="card"><b>{STATE.mut_rate:.2f}</b><span>adaptive mutation</span></div>
 <div class="card"><b>{len(STATE.benchmark_hits)}</b><span>benchmark passes</span></div>
 <div class="card"><b>{int(nxt)}s</b><span>next commit</span></div>
</div>
<h2>🏆 Best by Win Rate</h2>
<table><tr><th>agent</th><th>entry</th><th>conf</th><th>filters</th><th>SL</th><th>TP</th>
<th>trades</th><th>WR</th><th>PF</th><th>net</th><th>RR</th><th>DD</th></tr>{rows_wr}</table>
<h2>📏 Best by RR</h2>
<table><tr><th>agent</th><th>entry</th><th>conf</th><th>filters</th><th>SL</th><th>TP</th>
<th>trades</th><th>WR</th><th>PF</th><th>net</th><th>RR</th><th>DD</th></tr>{rows_rr}</table>
<h2>🎯 Best overall (fitness)</h2>
<table><tr><th>agent</th><th>entry</th><th>conf</th><th>filters</th><th>SL</th><th>TP</th>
<th>trades</th><th>WR</th><th>PF</th><th>net</th><th>RR</th><th>DD</th></tr>{rows_score}</table>
<div class="note">Genome = variable-length program (entry + confluence + 0-3 filters + exit).
Mutation rewrites blocks; crossover splices programs → UNLIMITED strategy space.
Self-coding: top agents → real Python (agents/Agent_XXXXX.py). Candle-open entry, no repaint,
conservative P&L, 1.06M GOLD M1. Benchmark: trades≥3000 & WR≥75% & RR≥3.
github.com/ryder777777/Trading-Agent-Ecosystem</div>
</body></html>"""

def status_server():
    port = int(os.environ.get("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main():
    load_data()
    prewarm_primitives()
    load_state()
    os.makedirs(EVO_DIR, exist_ok=True)
    os.makedirs(AGENT_DIR, exist_ok=True)
    rng = random.Random(int(time.time()))

    if not POP:
        init_population(rng)

    threading.Thread(target=status_server, daemon=True).start()

    cycle_delay = float(os.environ.get("CYCLE_DELAY", "5"))
    commit_every = int(os.environ.get("COMMIT_EVERY", "900"))
    digest_every = int(os.environ.get("DIGEST_EVERY", "7200"))

    make_charts(); save_state(); write_agent_files()
    git_commit()
    tg_send("🧬 Agent Ecosystem v3 — UNLIMITED STRATEGIES + SMART EVOLUTION LIVE!\n"
            "10,000 agents active · variable-length program genomes (agents apna code develop karte hain)\n"
            "tournament selection + adaptive mutation + multi-objective fitness (WR+RR+PF+net)\n"
            "github.com/ryder777777/Trading-Agent-Ecosystem")
    print("[startup] committed + notified")

    last_commit = time.time()
    last_digest = time.time()
    while True:
        try:
            one_cycle(rng)
            if STATE.cycles % 10 == 0:
                make_charts(); save_state(); write_agent_files()
            if time.time() - last_commit >= commit_every:
                make_charts(); save_state(); write_agent_files(); git_commit()
                last_commit = time.time()
                print(f"[commit] cycle {STATE.cycles} evals {STATE.evals} unique {STATE.unique} pop {len(POP)}")
            if time.time() - last_digest >= digest_every:
                tg_send(digest_text())
                last_digest = time.time()
                print("[digest] sent")
            if STATE.cycles % 25 == 0:
                print(f"[cycle {STATE.cycles}] evals={STATE.evals} unique={STATE.unique} "
                      f"pop={len(POP)} mut={STATE.mut_rate:.2f} bench={len(STATE.benchmark_hits)}")
            time.sleep(cycle_delay)
        except KeyboardInterrupt:
            make_charts(); save_state(); write_agent_files(); git_commit()
            break
        except Exception as exc:
            print(f"[error] {exc}")
            time.sleep(10)

if __name__ == "__main__":
    main()
