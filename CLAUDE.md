# streaming-poll-tg-bot

Behavior lives in `specs.md`. Text between `HUMAN START` and `HUMAN END` is the user's: never edit it. `product.md` is human-only too.

## Flow (GitHub Flow)

- `master` = production (@el_ping_bot). Only work the user has checked goes there.
- Each change: branch `feat/<name>` from master → tests first → commit after every green step.
- Try it on the test bot: `scripts/run.sh` (@audience_topic_inbox_bot, runs this working tree).
- User checks in Telegram and says it's good → merge into master → `scripts/deploy.sh` (tests, then restarts @el_ping_bot).
- Merge and deploy only on the user's explicit word.

## Where things are

- Test bot: tmux `audience_topic_inbox_bot`, log `logs/bot.log`, data `~/.local/share/streaming-poll-tg-bot/`.
- Prod bot: tmux `el_ping_bot`, code `~/deploy/streaming-poll-tg-bot-prod` (worktree of master, don't edit), log in its `logs/bot.log`, data `~/.local/share/streaming-poll-tg-bot-prod/`.
- Prod token + DB path: `~/.config/streaming-poll-tg-bot/prod.env` — never in the repo.
