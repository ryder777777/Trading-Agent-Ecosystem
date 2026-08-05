# 🤖 Trading Agent Ecosystem — Master Controller

Autonomous simulation harness of **10,000+ unique AI trading agents**, each with
its own DNA (strategy parameters, risk tolerance, session filters, psychology
seed), backtested on **1.06M real GOLD M1 candles** (2023–2026) under strict
execution rules:

- ✅ **Candle-OPEN execution only** — zero mid-candle entries (no repaint, no
  forward-looking bias). Signals are generated from closed candles exactly like
  the deployed `Meta-alerts` bot (`SECRET_LOGIC_B64` on Render).
- ✅ **No repainting indicators** — EMA / zones built from historical close/open only.
- ✅ **1:3 → 1:10 RR targeting** — fixed-RR TP mode (`tp = rr × sl`).
- ✅ **3,000+ trade benchmark** — statistical significance gate.
- 🧠 **Psychology layer** — hesitation, fear (post-loss streak), greed modelled,
  with **strict statistical override** for benchmark validation (benchmark always
  uses the emotion-free series).

## How it works

```
precompute (8 modes × 3 years signal CSVs, positionally verified vs real get_signal)
        ↓
10,000 agents × unique DNA (random sampling, seeded)
        ↓
vectorized conservative P&L (both TP+SL touch ⇒ SL first)
        ↓
metrics: trades / WR / PF / net / maxDD / achieved RR
        ↓
benchmark filter: trades ≥ 3000 AND WR ≥ 75% AND RR ≥ 3 AND net > 0
        ↓
memory log (JSONL) + charts + GitHub push + Telegram milestone broadcast
```

## Benchmark result (honest)

**0 / 10,000 agents passed** `WR ≥ 75% + RR ≥ 3 + ≥ 3000 trades` on this data.

Why (math): at 1:3 RR you only need 25% WR to break even — 75% WR at 1:3 RR is a
~5σ edge that does not exist in liquid gold M1. High WR and high RR are inversely
related on real data (efficiency frontier). The honest frontier found here:

| Style | Best WR | Best PF | Best net |
|---|---|---|---|
| High WR (quiet C1) | **51.3%** (4,079 trades) | 1.21 | +$139 |
| High PF (SL 0.5) | 35.6% | **1.30** | +$1,324 |
| High volume | 47.4% | 1.13 | +$964 |

Agents claiming 75–80% WR at 1:3+ RR are either repainting, overfit, or using
optimistic intra-candle assumptions — all of which fail live.

## Files

- `ecosystem.py` — master harness (DNA, engine, psychology, metrics, runner)
- `analyze.py` — cohort analysis + charts
- `results/cohort_results.jsonl` — all 10,000 agents' memory/performance logs
- `reports/` — charts + top agents CSV

## Run

```bash
N_AGENTS=10000 WORKERS=2 python3 ecosystem.py   # full cohort
python3 analyze.py                               # analysis + charts
```

*No secrets are stored in this repo. The private strategy logic (SECRET_LOGIC_B64)
stays on Render / local only.*
