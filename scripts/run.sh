#!/usr/bin/env bash
# Start or restart the bot in its tmux session.  Usage: scripts/run.sh [dev|prod]
#   dev  = @audience_topic_inbox_bot (test), settings from .env
#   prod = @el_ping_bot, .env + ~/.config/streaming-poll-tg-bot/prod.env (own token and database)
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-dev}" in
  dev)  session=streaming-poll-tg-bot;      log=logs/bot.log;      load="" ;;
  prod) session=streaming-poll-tg-bot-prod; log=logs/bot-prod.log
        load="set -a; . $HOME/.config/streaming-poll-tg-bot/prod.env; set +a;" ;;
  *) echo "usage: $0 [dev|prod]" >&2; exit 1 ;;
esac
mkdir -p logs
cmd="$load uv run python -m bot 2>&1 | tee -a $log"
if tmux has-session -t "$session" 2>/dev/null; then
  tmux send-keys -t "$session" C-c  # graceful stop: pending notifications are sent, queued media stays in the DB
  sleep 5
fi
if tmux has-session -t "$session" 2>/dev/null; then
  tmux respawn-pane -k -t "$session" "$cmd"
else
  tmux new-session -d -s "$session" -c "$PWD" "$cmd"
fi
echo "started in tmux session $session — watch: tmux attach -t $session  (log: $log)"
