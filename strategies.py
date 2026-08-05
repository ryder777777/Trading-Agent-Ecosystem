"""
STRATEGY LIBRARY — diverse, non-repainting signal generators for the evolution engine.

Every strategy: signal at candle i (computed ONLY from data up to i, closed candles)
=> entry at candle i+1 OPEN (zero mid-candle, no lookahead, no repaint).

Signal file format (per strategy x config x year): CSV (idx, time, side, price)
where idx = ENTRY candle index.
"""
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# rolling helpers
# ---------------------------------------------------------------------------
def ema(s, p):
    return pd.Series(s).ewm(span=p, adjust=False).mean().to_numpy()

def sma(s, p):
    return pd.Series(s).rolling(p, min_periods=p).mean().to_numpy()

def rsi_wilder(c, p=14):
    s = pd.Series(c)
    d = s.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    ru = up.ewm(alpha=1/p, adjust=False).mean()
    rd = dn.ewm(alpha=1/p, adjust=False).mean()
    rs = ru / rd.replace(0, np.nan)
    r = 100 - 100 / (1 + rs)
    return r.fillna(50).to_numpy()

def atr_wilder(h, l, c, p=14):
    pc = pd.Series(c).shift(1)
    tr = pd.concat([pd.Series(h) - pd.Series(l),
                    (pd.Series(h) - pc).abs(),
                    (pd.Series(l) - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/p, adjust=False).mean().to_numpy()

def stoch(c, h, l, kp=14, d=3):
    ll = pd.Series(l).rolling(kp, min_periods=kp).min()
    hh = pd.Series(h).rolling(kp, min_periods=kp).max()
    k = 100 * (pd.Series(c) - ll) / (hh - ll).replace(0, np.nan)
    k = k.fillna(50)
    dd = k.rolling(d, min_periods=d).mean()
    return k.to_numpy(), dd.to_numpy()

def supertrend_np(h, l, c, p=10, m=3.0):
    a = atr_wilder(h, l, c, p)
    hl = (np.array(h) + np.array(l)) / 2.0
    fu = hl + m * a
    fl = hl - m * a
    n = len(c)
    st = np.empty(n); d = np.empty(n)
    st[0] = fu[0]; d[0] = 1
    for i in range(1, n):
        fu[i] = fu[i] if (fu[i] < fu[i-1] or c[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = fl[i] if (fl[i] > fl[i-1] or c[i-1] < fl[i-1]) else fl[i-1]
        if st[i-1] == fu[i-1]:
            st[i] = fu[i] if c[i] <= fu[i] else fl[i]
            d[i] = -1 if c[i] <= fu[i] else 1
        else:
            st[i] = fl[i] if c[i] >= fl[i] else fu[i]
            d[i] = 1 if c[i] >= fl[i] else -1
    return st, d

def donchian_high(h, n):
    return pd.Series(h).rolling(n, min_periods=n).max().shift(1).to_numpy()

def donchian_low(l, n):
    return pd.Series(l).rolling(n, min_periods=n).min().shift(1).to_numpy()

def cross_up(a, b):
    """a crosses above b at current index (both arrays)."""
    a = np.asarray(a); b = np.asarray(b)
    prev = np.concatenate([[False], (a > b)[:-1]])
    return (a > b) & ~prev

def cross_dn(a, b):
    a = np.asarray(a); b = np.asarray(b)
    prev = np.concatenate([[False], (a < b)[:-1]])
    return (a < b) & ~prev

def to_signal(c, h, l, o, buy, sell, min_i=2):
    """buy/sell bool arrays on closed candles -> entry idx arrays (i+1)."""
    n = len(c)
    bidx = np.where(buy)[0] + 1
    sidx = np.where(sell)[0] + 1
    bidx = bidx[(bidx >= min_i) & (bidx < n)]
    sidx = sidx[(sidx >= min_i) & (sidx < n)]
    return bidx, sidx

# ---------------------------------------------------------------------------
# STRATEGY DEFINITIONS: each returns (buy_idx, sell_idx) entry indices
# ---------------------------------------------------------------------------
def s_ema_cross(c, h, l, o, f, s):
    ef, es = ema(c, f), ema(c, s)
    return to_signal(c, h, l, o, cross_up(ef, es), cross_dn(ef, es))

def s_sma_cross(c, h, l, o, f, s):
    sf_, ss = sma(c, f), sma(c, s)
    return to_signal(c, h, l, o, cross_up(sf_, ss), cross_dn(sf_, ss))

def s_rsi_mr(c, h, l, o, p, lo, hi):
    r = rsi_wilder(c, p)
    return to_signal(c, h, l, o, cross_up(r, np.full(len(c), lo)),
                     cross_dn(r, np.full(len(c), hi)))

def s_rsi_mom(c, h, l, o, p):
    r = rsi_wilder(c, p)
    return to_signal(c, h, l, o, cross_up(r, np.full(len(c), 50)),
                     cross_dn(r, np.full(len(c), 50)))

def s_macd(c, h, l, o, f, s, g):
    ef, es = ema(c, f), ema(c, s)
    line = ef - es
    sig = ema(line, g)
    return to_signal(c, h, l, o, cross_up(line, sig), cross_dn(line, sig))

def s_stoch(c, h, l, o, p, dd, lo, hi):
    k, d = stoch(c, h, l, p, dd)
    return to_signal(c, h, l, o,
                     cross_up(k, d) & (k < hi),
                     cross_dn(k, d) & (k > lo))

def s_boll_mr(c, h, l, o, p, k):
    mid = sma(c, p)
    sd = pd.Series(c).rolling(p, min_periods=p).std().to_numpy()
    lo_, hi_ = mid - k * sd, mid + k * sd
    return to_signal(c, h, l, o, c < lo_, c > hi_)

def s_boll_break(c, h, l, o, p, k):
    mid = sma(c, p)
    sd = pd.Series(c).rolling(p, min_periods=p).std().to_numpy()
    lo_, hi_ = mid - k * sd, mid + k * sd
    return to_signal(c, h, l, o, cross_up(c, hi_), cross_dn(c, lo_))

def s_donchian_break(c, h, l, o, n):
    hh = donchian_high(h, n); ll = donchian_low(l, n)
    return to_signal(c, h, l, o, c > hh, c < ll)

def s_donchian_mr(c, h, l, o, n):
    hh = donchian_high(h, n); ll = donchian_low(l, n)
    return to_signal(c, h, l, o, c < ll, c > hh)

def s_atr_break(c, h, l, o, n, mult):
    hh = donchian_high(h, n); ll = donchian_low(l, n)
    a = atr_wilder(h, l, c, 14)
    amed = pd.Series(a).rolling(200, min_periods=50).median().to_numpy()
    filt = a > mult * amed
    return to_signal(c, h, l, o, (c > hh) & filt, (c < ll) & filt)

def s_roc(c, h, l, o, n, thr):
    roc = np.full(len(c), 0.0)
    roc[n:] = (c[n:] / c[:-n] - 1) * 100
    return to_signal(c, h, l, o, cross_up(roc, np.full(len(c), thr)),
                     cross_dn(roc, np.full(len(c), -thr)))

def s_roc_zero(c, h, l, o, n):
    roc = np.full(len(c), 0.0)
    roc[n:] = (c[n:] / c[:-n] - 1) * 100
    return to_signal(c, h, l, o, cross_up(roc, np.zeros(len(c))),
                     cross_dn(roc, np.zeros(len(c))))

def s_supertrend(c, h, l, o, p, m):
    _, d = supertrend_np(h, l, c, p, m)
    return to_signal(c, h, l, o, cross_up(d, np.zeros(len(c))),
                     cross_dn(d, np.zeros(len(c))))

def s_engulfing(c, h, l, o, *_):
    prev_green = c[:-1] >= o[:-1]
    cur_red = c[1:] < o[1:]
    bear = prev_green & cur_red & (c[1:] > o[:-1]) & (o[1:] < c[:-1])
    prev_red = c[:-1] < o[:-1]
    cur_green = c[1:] >= o[1:]
    bull = prev_red & cur_green & (c[1:] > o[:-1]) & (o[1:] < c[:-1])
    buy = np.zeros(len(c), bool); sell = np.zeros(len(c), bool)
    buy[1:] = bull; sell[1:] = bear
    return to_signal(c, h, l, o, buy, sell)

def s_pinbar(c, h, l, o, ratio):
    body = (c - o).__abs__()
    upper = h - np.maximum(c, o)
    lower = np.minimum(c, o) - l
    bull = (lower >= ratio * body) & (upper <= body) & (c > o)
    bear = (upper >= ratio * body) & (lower <= body) & (c < o)
    return to_signal(c, h, l, o, bull, bear)

def s_insidebar(c, h, l, o, *_):
    # candle i is INSIDE candle i-1 (mother bar). Breakout at candle i+1.
    inside = (h[1:] < h[:-1]) & (l[1:] > l[:-1])
    buy = np.zeros(len(c), bool); sell = np.zeros(len(c), bool)
    for i in np.where(inside)[0]:
        j = i + 1            # candle j (0-based) is inside candle j-1
        if j + 1 < len(c):   # breakout candle j+1
            if c[j + 1] > h[j - 1]:
                buy[j + 1] = True
            elif c[j + 1] < l[j - 1]:
                sell[j + 1] = True
    return to_signal(c, h, l, o, buy, sell)

def s_nr7(c, h, l, o, *_):
    c = np.asarray(c); h = np.asarray(h); l = np.asarray(l); o = np.asarray(o)
    rng = (h - l)
    n = len(c)
    buy = np.zeros(n, bool); sell = np.zeros(n, bool)
    for i in range(7, n):
        if rng[i] < rng[i-7:i].min() * 0.8:
            if c[i] > h[i-1]:
                buy[i] = True
            elif c[i] < l[i-1]:
                sell[i] = True
    return to_signal(c, h, l, o, buy, sell)

def s_ma_pullback(c, h, l, o, fast, trend):
    mf = sma(c, fast); mt = sma(c, trend)
    up = c > mt
    buy = up & (l <= mf) & (c >= mf)
    dn = c < mt
    sell = dn & (h >= mf) & (c <= mf)
    return to_signal(c, h, l, o, buy, sell)

def s_trend_pullback(c, h, l, o, fast, trend):
    ef = ema(c, fast); et = ema(c, trend)
    up = c > et
    buy = up & (l <= ef) & (c >= ef)
    dn = c < et
    sell = dn & (h >= ef) & (c <= ef)
    return to_signal(c, h, l, o, buy, sell)

def s_doji_rev(c, h, l, o, thr, *_):
    body = (c - o).__abs__()
    rng = (h - l).replace(0, 1) if hasattr(h - l, "replace") else (np.asarray(h) - np.asarray(l))
    rng = np.where(rng == 0, 1e-9, rng)
    doji = body < thr * rng
    buy = np.zeros(len(c), bool); sell = np.zeros(len(c), bool)
    # doji at low after decline -> reversal up ; doji at high after rise -> down
    for i in np.where(doji)[0]:
        if i + 1 < len(c):
            if c[i] < c[i - 1] and c[i + 1] > c[i]:
                buy[i + 1] = True
            elif c[i] > c[i - 1] and c[i + 1] < c[i]:
                sell[i + 1] = True
    return to_signal(c, h, l, o, buy, sell)

# ---------------------------------------------------------------------------
# REGISTRY: name -> (function, param grid list of dicts)
# ---------------------------------------------------------------------------
STRATS = {
    "ema_cross":      (s_ema_cross, [dict(f=9, s=21), dict(f=5, s=20), dict(f=10, s=30),
                                     dict(f=21, s=55), dict(f=50, s=200)]),
    "sma_cross":      (s_sma_cross, [dict(f=9, s=21), dict(f=20, s=50), dict(f=50, s=200)]),
    "rsi_mr":         (s_rsi_mr,    [dict(p=14, lo=30, hi=70), dict(p=14, lo=25, hi=75),
                                     dict(p=7, lo=30, hi=70)]),
    "rsi_mom":        (s_rsi_mom,   [dict(p=14), dict(p=7)]),
    "macd":           (s_macd,      [dict(f=12, s=26, g=9), dict(f=8, s=21, g=5)]),
    "stoch":          (s_stoch,     [dict(p=14, dd=3, lo=30, hi=70), dict(p=7, dd=3, lo=20, hi=80)]),
    "boll_mr":        (s_boll_mr,   [dict(p=20, k=2.0), dict(p=20, k=2.5), dict(p=50, k=2.0)]),
    "boll_break":     (s_boll_break,[dict(p=20, k=2.0), dict(p=20, k=2.5)]),
    "donchian_break": (s_donchian_break, [dict(n=20), dict(n=55), dict(n=100)]),
    "donchian_mr":    (s_donchian_mr, [dict(n=20), dict(n=55)]),
    "atr_break":      (s_atr_break, [dict(n=20, mult=1.0), dict(n=55, mult=1.5)]),
    "roc":            (s_roc,       [dict(n=10, thr=0.05), dict(n=20, thr=0.05),
                                     dict(n=50, thr=0.1), dict(n=100, thr=0.2)]),
    "roc_zero":       (s_roc_zero,  [dict(n=10), dict(n=20)]),
    "supertrend":     (s_supertrend, [dict(p=10, m=3.0), dict(p=14, m=3.0), dict(p=7, m=2.0)]),
    "engulfing":      (s_engulfing, [dict()]),
    "pinbar":         (s_pinbar,    [dict(ratio=2.0), dict(ratio=3.0)]),
    "insidebar":      (s_insidebar, [dict()]),
    "nr7":            (s_nr7,       [dict()]),
    "ma_pullback":    (s_ma_pullback, [dict(fast=20, trend=50), dict(fast=20, trend=200)]),
    "trend_pullback": (s_trend_pullback, [dict(fast=20, trend=200), dict(fast=50, trend=200)]),
    "doji_rev":       (s_doji_rev,  [dict(thr=0.1), dict(thr=0.05)]),
}

STRAT_NAMES = list(STRATS.keys())

def strat_cfgs(name):
    return STRATS[name][1]

def run_strat(name, cfg, c, h, l, o):
    c = np.asarray(c, dtype=float); h = np.asarray(h, dtype=float)
    l = np.asarray(l, dtype=float); o = np.asarray(o, dtype=float)
    return STRATS[name][0](c, h, l, o, **cfg)
