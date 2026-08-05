"""Agent Agent_02160 — strategy code auto-developed by evolution engine v3.
Candle-open entry: signal on closed candle i -> entry at i+1 open.
No repaint, conservative P&L. Data: GOLD M1 2023-2026."""
import numpy as np
import pandas as pd

def ema(a,n): return pd.Series(a).ewm(span=n,adjust=False).mean().to_numpy()
def sma(a,n): return pd.Series(a).rolling(n,min_periods=n).mean().to_numpy()
def std(a,n): return pd.Series(a).rolling(n,min_periods=n).std().to_numpy()
def rsi(c,p=14):
    s=pd.Series(c); d=s.diff(); up=d.clip(lower=0); dn=(-d).clip(lower=0)
    ru=up.ewm(alpha=1/p,adjust=False).mean(); rd=dn.ewm(alpha=1/p,adjust=False).mean()
    rs=ru/rd.replace(0,np.nan); return (100-100/(1+rs)).fillna(50).to_numpy()
def atr(h,l,c,n=14):
    pc=pd.Series(c).shift(1)
    tr=pd.concat([pd.Series(h)-pd.Series(l),(pd.Series(h)-pc).abs(),(pd.Series(l)-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False).mean().to_numpy()
def donch(h,l,n): return pd.Series(h).rolling(n,min_periods=n).max().to_numpy(), pd.Series(l).rolling(n,min_periods=n).min().to_numpy()
def st(h,l,c,p,m):
    a=atr(h,l,c,p); hl=(np.asarray(h)+np.asarray(l))/2; fu=hl+m*a; fl=hl-m*a; n=len(c); d=np.empty(n); d[0]=1
    for i in range(1,n):
        fu[i]=fu[i] if (fu[i]<fu[i-1] or c[i-1]>fu[i-1]) else fu[i-1]
        fl[i]=fl[i] if (fl[i]>fl[i-1] or c[i-1]<fl[i-1]) else fl[i-1]
        d[i]=-1 if c[i]<=fu[i] else 1 if c[i]>=fl[i] else d[i-1]
    return d
def stoch(c,h,l,p,d):
    ll=pd.Series(l).rolling(p,min_periods=p).min(); hh=pd.Series(h).rolling(p,min_periods=p).max()
    k=100*(pd.Series(c)-ll)/(hh-ll).replace(0,np.nan); k=k.fillna(50); return k.to_numpy(), k.rolling(d,min_periods=d).mean().to_numpy()
def roc(c,n): r=np.zeros(len(c)); r[n:]=(c[n:]/c[:-n]-1)*100; return r
def cross_up(a,b): a=np.asarray(a); b=np.asarray(b); up=a>b; return up&~np.concatenate([[False],up[:-1]])
def cross_dn(a,b): a=np.asarray(a); b=np.asarray(b); dn=a<b; return dn&~np.concatenate([[False],dn[:-1]])
def engulfing(c,o):
    n=len(c); buy=np.zeros(n,bool); sell=np.zeros(n,bool)
    buy[1:]=(c[:-1]<o[:-1])&(c[1:]>=o[1:])&(c[1:]>o[:-1])&(o[1:]<c[:-1])
    sell[1:]=(c[:-1]>=o[:-1])&(c[1:]<o[1:])&(c[1:]>o[:-1])&(o[1:]<c[:-1])
    return buy,sell
def pinbar(c,h,l,o,ratio):
    body=np.abs(c-o); up=h-np.maximum(c,o); lo=np.minimum(c,o)-l
    return (lo>=ratio*body)&(up<=body)&(c>o), (up>=ratio*body)&(lo<=body)&(c<o)
def nr7(h,l,c):
    rng=h-l; rmin=pd.Series(rng).rolling(7,min_periods=7).min().to_numpy(); tight=rng<np.roll(rmin,1)*0.8
    up=np.zeros(len(c),bool); dn=np.zeros(len(c),bool); up[1:]=c[1:]>h[:-1]; dn[1:]=c[1:]<l[:-1]
    return tight&up, tight&dn
def doji_rev(c,h,l,o,thr):
    n=len(c); body=np.abs(c-o); rng=np.where(h-l==0,1e-9,h-l); doji=body<thr*rng
    tb=doji&np.concatenate([[False],c[1:]<c[:-1]])&np.concatenate([np.zeros(2,bool),c[2:]>c[1:-1]])
    ts=doji&np.concatenate([[False],c[1:]>c[:-1]])&np.concatenate([np.zeros(2,bool),c[2:]<c[1:-1]])
    buy=np.zeros(n,bool); sell=np.zeros(n,bool); buy[1:]=tb[:-1]; sell[1:]=ts[:-1]; return buy,sell

def entry_signals(c, h, l, o):
    # ENTRY BLOCK: stoch {'p': 14, 'd': 3}
    k,ks=stoch(c,h,l,14,3)
    buy=cross_up(k,ks)
    sell=cross_dn(k,ks)

    return buy, sell

EXIT = {"sl_mode": "fixed", "sl": 0.82, "sl_atr": 2.5, "tp_mode": "trail", "rr": 9.2, "trail": 1.5}
FILTERS = []
RISK = 1.0
COOL = 0