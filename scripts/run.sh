#!/usr/bin/env bash
# Start or restart the TEST bot (@audience_topic_inbox_bot) from this working tree.  Usage: scripts/run.sh
# Production is deployed from master with scripts/deploy.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
session=streaming-poll-tg-bot
mkdir -p logs
cmd="uv run python -m bot 2>&1 | tee -a logs/bot.log"
if tmux has-session -t "$session" 2>/dev/null; then
  tmux send-keys -t "$session" C-c  # graceful stop: pending notifications are sent, queued media stays in the DB
  sleep 5
fi
if tmux has-session -t "$session" 2>/dev/null; then
  tmux respawn-pane -k -t "$session" "$cmd"
else
  tmux new-session -d -s "$session" -c "$PWD" "$cmd"
fi
echo "started test bot in tmux session $session — watch: tmux attach -t $session  (log: logs/bot.log)"
