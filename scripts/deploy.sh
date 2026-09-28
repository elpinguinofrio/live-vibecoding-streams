#!/usr/bin/env bash
# Deploy master to production (@el_ping_bot).  Usage: scripts/deploy.sh
# Production runs from its own copy of master on local disk, never from the working tree being edited.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
prod="$HOME/deploy/streaming-poll-tg-bot-prod"
session=el_ping_bot
env_file="$HOME/.config/streaming-poll-tg-bot/prod.env"  # TELEGRAM_BOT_TOKEN + DB_PATH of the prod bot
[ -f "$env_file" ] || { echo "missing $env_file" >&2; exit 1; }

if [ -d "$prod" ]; then
  git -C "$prod" checkout -q --detach master
else
  mkdir -p "$(dirname "$prod")"
  git -C "$repo" worktree add -q --detach "$prod" master
fi
ln -sfn "$repo/.env" "$prod/.env"  # shared settings (admins, models); prod.env overrides token and DB
cd "$prod"
uv sync -q --frozen
uv run pytest -q  # master must pass before it reaches the audience
mkdir -p logs

cmd="set -a; . $env_file; set +a; uv run python -m bot 2>&1 | tee -a $prod/logs/bot.log"
if tmux has-session -t "$session" 2>/dev/null; then
  tmux send-keys -t "$session" C-c  # graceful stop: pending notifications are sent, queued media stays in the DB
  sleep 5
fi
if tmux has-session -t "$session" 2>/dev/null; then
  tmux respawn-pane -k -t "$session" -c "$prod" "$cmd"
else
  tmux new-session -d -s "$session" -c "$prod" "$cmd"
fi
echo "deployed $(git rev-parse --short HEAD) — watch: tmux attach -t $session  (log: $prod/logs/bot.log)"
