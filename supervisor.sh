#!/usr/bin/env bash
# Supervisor: keeps evolution engine + telegram responder alive (auto-restart on crash)
set -u
cd /home/user/agent-ecosystem

export GITHUB_TOKEN="$(cat /tmp/ghtok/token 2>/dev/null)"
export TG_BOT="$(grep TG_BOT /tmp/tg_env | cut -d= -f2-)"
export TG_CHAT="$(grep TG_CHAT /tmp/tg_env | cut -d= -f2-)"
export PORT=8080
export BATCH=1500
export CYCLE_DELAY=5
export COMMIT_EVERY=900
export DIGEST_EVERY=7200

echo "[supervisor] starting at $(date)"

# --- evolution engine (resumes from disk state) ---
(
  while true; do
    echo "[supervisor] evolution start $(date)"
    python3 -u evolution.py
    echo "[supervisor] evolution EXITED rc=$? -> restart in 5s"
    sleep 5
  done
) &
EVO_PID=$!

# --- telegram responder (resumes from disk leaderboard) ---
(
  while true; do
    echo "[supervisor] responder start $(date)"
    python3 -u telegram_responder.py
    echo "[supervisor] responder EXITED rc=$? -> restart in 5s"
    sleep 5
  done
) &
RESP_PID=$!

echo "[supervisor] evo pid=$EVO_PID resp pid=$RESP_PID — supervising"

# keep alive; if either child exits permanently, still wait (inner loops restart)
wait $EVO_PID
wait $RESP_PID
