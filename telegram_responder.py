"""
TELEGRAM COMMAND RESPONDER — Agent Ecosystem

Bot ko in commands bhejne par live evolution state se jawab deta hai:
  /help     -> commands list
  /status   -> uptime, cycles, evals, unique, benchmark
  /topwr    -> top winrate agents (dashboard jaisa)
  /toppf    -> top profit factor
  /topnet   -> top net P&L
  /toprr    -> top achieved RR
  /topscore -> best overall (score)
  /bench    -> benchmark (75% WR + 1:3 RR + 3000 trades) status

Security: sirf owner chat (TG_CHAT) ko reply karta hai. State files se
live data padhta hai (kabhi memory me kuch store nahi karta).

Run:
  TG_BOT=... TG_CHAT=... python3 telegram_responder.py
"""
import json
import os
import time
import traceback

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
EVO = os.path.join(ROOT, "evolution")
STATE_FILE = os.path.join(EVO, "state.json")
LB_FILE = os.path.join(EVO, "leaderboard.json")

TOKEN = os.environ.get("TG_BOT", "")
ALLOWED = os.environ.get("TG_CHAT", "")

API = f"https://api.telegram.org/bot{TOKEN}"


# ----------------------------------------------------------------------------
# state readers (fresh from disk each command)
# ----------------------------------------------------------------------------
def read_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def read_leaderboard():
    try:
        with open(LB_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


# ----------------------------------------------------------------------------
# formatting
# ----------------------------------------------------------------------------
def fmt_agent(m, idx=0):
    g = m["genes"]
    line = (
        f"{idx+1}. {g['mode']} | SL={g['sl']}"
        + (f" (ATR{g['sl_atr']})" if g["sl_atr"] else "")
        + f" | TP={g['tp']}" + (f"/RR{g['rr']}" if g["tp"] == "rr" else "")
        + f" | quiet={g['quiet'] or '-'}"
        + f" | sess={g['sess'] or '-'}"
        + f" | ema={g['emaN'] or '-'}"
        + f" | body={g['body']}"
        + f" | cool={g['cool']} | risk={g['risk']}\n"
        f"    trades={m['trades']} | W={m['wins']} L={m['losses']} | "
        f"WR={m['wr']:.1f}% | PF={m['pf']:.2f} | net=${m['net']:.0f} | "
        f"RR={m['rr']:.2f} | maxDD=${m['maxdd']:.1f}"
    )
    return line


def cmd_help():
    return (
        "🤖 Agent Ecosystem — bot commands:\n"
        "/status — engine status (uptime, cycles, evals)\n"
        "/topwr — top winrate agents\n"
        "/toppf — top profit factor\n"
        "/topnet — top net P&L\n"
        "/toprr — top achieved RR\n"
        "/topscore — best overall (score)\n"
        "/bench — benchmark (WR≥75% + RR≥3 + ≥3000 trades) status\n"
        "/help — ye list\n\n"
        "Data: evolution state (GOLD M1 2023-2026, candle-open, conservative P&L)."
    )


def cmd_status():
    st = read_state()
    up = time.time() - st.get("started", time.time())
    return (
        "🧬 Agent Ecosystem — STATUS\n"
        f"uptime: {up/3600:.1f}h | cycles: {st.get('cycles', 0)}\n"
        f"evaluations: {st.get('evals', 0):,} | unique strategies: {st.get('unique', 0):,}\n"
        f"benchmark passes: {st.get('benchmark', 0)}\n"
        "📦 github.com/ryder777777/Trading-Agent-Ecosystem"
    )


def cmd_top(key, title, n=5):
    lb = read_leaderboard()
    items = lb.get(key, [])
    if not items:
        return f"{title}: abhi koi data nahi (engine seed kar raha hai)"
    lines = [f"🏆 {title} (>=500 trades):"]
    for i, m in enumerate(items[:n]):
        lines.append(fmt_agent(m, i))
    return "\n".join(lines)


def cmd_bench():
    lb = read_leaderboard()
    bench = []
    for key in lb:
        for m in lb[key]:
            if m.get("benchmark") and m["name"] not in [b["name"] for b in bench]:
                bench.append(m)
    if not bench:
        return (
            "🎯 Benchmark status: 0 agents passed (WR≥75% + RR≥3 + ≥3000 trades).\n"
            "Best verified so far — top WR ~" +
            (f"{lb['wr'][0]['wr']:.1f}%" if lb.get("wr") else "-") +
            " · top PF ~" + (f"{lb['pf'][0]['pf']:.2f}" if lb.get("pf") else "-") +
            ".\n\nRealistic frontier: WR 45-54%, PF 1.1-1.7, RR 1-6. "
            "75%+ WR @ 1:3 RR is data pe exist nahi karta (math)."
        )
    lines = ["🎉 MILESTONE AGENT(S):"]
    for m in bench:
        lines.append(fmt_agent(m))
    return "\n".join(lines)


HANDLERS = {
    "/help": cmd_help,
    "/start": cmd_help,
    "/status": cmd_status,
    "/topwr": lambda: cmd_top("wr", "Top Win Rate"),
    "/toppf": lambda: cmd_top("pf", "Top Profit Factor"),
    "/topnet": lambda: cmd_top("net", "Top Net P&L"),
    "/toprr": lambda: cmd_top("rr", "Top Achieved RR"),
    "/topscore": lambda: cmd_top("score", "Best Overall (score)"),
    "/bench": cmd_bench,
}


# ----------------------------------------------------------------------------
# long-polling loop
# ----------------------------------------------------------------------------
def send(chat, text):
    try:
        requests.post(f"{API}/sendMessage", json={"chat_id": chat, "text": text},
                      timeout=10)
    except Exception:
        pass


def main():
    if not TOKEN:
        print("TG_BOT missing"); return
    # ensure no webhook conflict
    try:
        r = requests.get(f"{API}/getWebhookInfo", timeout=10).json()
        if r.get("ok") and r.get("result", {}).get("url"):
            requests.post(f"{API}/deleteWebhook", timeout=10)
    except Exception:
        pass
    offset = 0
    print("[tg] responder started, polling...")
    while True:
        try:
            r = requests.get(f"{API}/getUpdates",
                             params={"timeout": 30, "offset": offset}, timeout=35)
            data = r.json()
            if not data.get("ok"):
                time.sleep(3)
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                chat = str(msg["chat"]["id"])
                if chat != str(ALLOWED):
                    continue
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                cmd = text.split()[0].lower()
                handler = HANDLERS.get(cmd)
                if handler:
                    try:
                        reply = handler()
                    except Exception:
                        reply = "⚠️ Error: " + traceback.format_exc(limit=1)
                    send(chat, reply)
                    print(f"[tg] replied to {cmd}")
        except Exception as exc:
            print(f"[tg] poll error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
