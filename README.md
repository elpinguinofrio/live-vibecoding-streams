# Бот: темы от аудитории

**Бот:** https://t.me/audience_topic_inbox_bot

Тестовый проект, сделанный на стриме: https://youtube.com/live/2P_Aqd70dJs?feature=share

## Что умеет

- Зрители пишут боту тему текстом или голосом.
- Голосовые расшифровываются, всё сохраняется, после сохранения бот ставит 👍.
- Админам приходит уведомление о каждом новом предложении.
- `/summary` — сводка всех предложений по темам (только для админов).
- `/admins`, `/addadmin <id или @username>`, `/removeadmin <id или @username>` — управление админами прямо в боте.
  Стартовые админы задаются в `ADMIN_USER_IDS` (по умолчанию @real_turkish_wolf и @lebed2045); последнего админа удалить нельзя.

## Запуск

1. Заполнить `.env` по образцу `.env.example`.
2. `uv sync`
3. `scripts/run.sh` — тестовый бот (@audience_topic_inbox_bot) в tmux-сессии `streaming-poll-tg-bot`
4. Продакшн (@el_ping_bot) = ветка `master`: слить изменения в master и `scripts/deploy.sh`
   (токен и своя база — в `~/.config/streaming-poll-tg-bot/prod.env`). Процесс — в `CLAUDE.md`.

Смотреть лог: `tmux attach -t streaming-poll-tg-bot` (выйти: Ctrl-b, затем d)

Тесты: `uv run pytest`
