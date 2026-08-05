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

---

## 🧬 Autonomous Evolution Engine (24h loop)

`evolution.py` — a continuous self-improving backtest loop:

- **Every cycle:** elite selection → crossover + mutation (code mutation) + random
  exploration → new genomes evaluated on 1.06M GOLD M1 candles.
- **Genome space:** mode, SL (fixed or ATR-based), TP (close / fixed-RR 2-10x),
  quiet-C1 filter, session window, C1 direction, EMA50/100/200 alignment, body
  filter, cooldown-after-loss, risk sizing.
- **Rotating objectives:** score / WR / PF / RR — agents compete on different goals.
- **Persistence (no ephemeral storage):** every 15 min auto-commits to GitHub
  (`evolution/` — registry, leaderboard, charts, state) + Telegram digest every 2h
  + instant milestone broadcast (WR≥75% & RR≥3 & ≥3,000 trades).
- **Resumable:** state.json + registry_top.json → restart continues from last commit.
- **Live dashboard:** HTTP server (PORT=8080) shows uptime / cycles / evals /
  leaderboards — auto-refreshing.

### Run
```bash
GITHUB_TOKEN=... TG_BOT=... TG_CHAT=... PORT=8080 BATCH=1500 CYCLE_DELAY=5 \
  COMMIT_EVERY=900 DIGEST_EVERY=7200 python3 -u evolution.py
```

**Honest metric:** benchmark gate stays at trades≥3000 & WR≥75% & RR≥3 & net>0.
The engine explores and reports the *real* efficiency frontier — it does not fake
75%+ WR. Milestone release + broadcast fire only on a verified pass.

---

## 🧬 v2 — DIVERSE STRATEGY SPACE (upgrade)

Ab sirf 8 zone-modes nahi — **22 strategy families** explore ho rahi hain,
har agent apni alag strategy ke saath (10,000+ agents):

| Family | Type | Examples |
|---|---|---|
| zones (aapki logic) | POI zones | SUPER_LOOSE etc. (8 modes) |
| ema_cross / sma_cross / macd / supertrend | Trend following | fast/slow cross |
| rsi_mr / rsi_mom / boll_mr / donchian_mr | Mean reversion | oversold/overbought |
| donchian_break / boll_break / atr_break | Breakout | N-bar highs/lows |
| roc / roc_zero | Momentum | ROC thresholds |
| engulfing / pinbar / insidebar / nr7 / doji_rev | Candle patterns | reversal signals |
| ma_pullback / trend_pullback | Pullback | EMA/SMA dips |

- `strategies.py` — 21 vectorized non-repainting signal generators
- `precompute_signals.py` — 147 signal files (strat × config × year), candle-open entry
- `signals/` — precomputed signal CSVs (non-repainting, entry at next open)
- Genome = (strat, config) + exits (SL/ATR-SL, TP close/RR) + filters + risk
- Dashboard ab strategy family dikhata hai

**v2 early results (10 min exploration):**
- stoch → net +$16,322 · PF 1.30 (325k trades)
- donchian_mr → +$2,253 · PF 1.41
- macd → RR 4.29 · nr7 → RR 6.43 · boll_mr → WR 53.1%

*Sab conservative P&L, candle-open, no repaint — same benchmark gate.*

---

## 🧬 v3 — UNLIMITED STRATEGY SPACE + SELF-CODING AGENTS (final)

**Genome = variable-length program** (entry primitive + optional confluence + 0-3
filters + exit SL/TP + risk + cooldown). Mutation adds/removes/rewrites blocks,
crossover splices programs → **unlimited** strategy combinations from 18 primitives.

**Smart evolution:** tournament selection (k=3) · adaptive mutation rate
(auto-explore when stuck / exploit when improving) · 10,000-agent live population
with generational replacement (weakest slots replaced each cycle) · multi-objective
fitness = net × √PF × WR-boost × RR-boost × volume × DD-penalty.

**Self-coding:** top agents rendered to real Python — `agents/Agent_XXXXX.py`
(entry signals + exit + filters), INDEX.md with full DNA.

**v3 early results (16 min):**
- Best WR: **57.7%** (stoch) · Best RR: **10.22** (pinbar) · Best net: **+$22,105** (stoch)
- 56,000+ unique programs explored · 10,000 agents active

Same honest rules: candle-open entry, no repaint, conservative P&L, benchmark
gate trades≥3000 & WR≥75% & RR≥3.
