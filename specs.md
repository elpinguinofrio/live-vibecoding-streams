# streaming-poll-tg-bot — spec (Telegram: @audience_topic_inbox_bot)

What the bot does. Edit freely; the code follows this file.

> **Rule:** everything between `HUMAN START` and `HUMAN END` is yours.
> Claude never edits, reformats, or deletes it — only reads it.
> Claude writes only below `HUMAN END`.

<!-- HUMAN START -->
vТупой гондон Блять. Он только скажи, что на данный момент принимает только текст. 
vЕсли когда сообщение просто принялось, то он такой этот окей эмоджи посылает. 
vА когда она уже транскрибировалась и чуваку заделиверилась этому как называется, админ прочел, то можно уже фам зап тогда высылать. 
vas for reply notification, either just next message from an admin or literally use comment of reply. So you have to handle both. 
vI'm not sure two emoji should make sense. Maybe let's do this. if like I assume when person sends the message, it also has like one and two check marks, right? And when it saves and transcribes, like or or whatever, accepted without error, it will give this okay emoji.

Don't do automatic thumbs up like never. And admin, like by default, have ability to you know reply with emojis. So basically bot gives maybe buttons to admin like in in a message is where like a simple way to give it in in UI like fast reply kind of things and it's able to reply with like thumbs up emoji you know just to press a button or whatever suggestions is to reply from this whatever model like use this group you whatever biggest available model or like admin doesn't do anything or admin replies with some voice or video message or or or a text 
<!-- HUMAN END -->

## Viewers

- Menu shows only `/start`, which explains how to send a topic.
- Accepted: text, voice, video circle, video, photo.
- Telegram's own ✓/✓✓ show sent/read. The bot adds one reaction, 👌, once the message is saved (and transcribed) without errors. The bot never puts 👍 by itself.
- Voice/circle/video are transcribed. No speech → saved as «(без речи)». Photo → its caption, or «(фото без подписи)».
- Anything else (files, stickers) → «Я принимаю текст, голосовые, кружки, видео и фото.» Not saved.
- Over ~19 MB → told it's too big. Not saved.
- Never see internal errors. Failed transcription or save is retried every 10 s silently; a queue keeps concurrent senders and survives restarts. After ~5 min of failures it is saved as «(не удалось распознать)».
- Admin answers (text, voice, circle, video, or a reaction) arrive from the bot, on their message; which admin answered is never shown.

## Admins

- Start as @real_turkish_wolf and @lebed2045; managed with `/admins`, `/addadmin`, `/removeadmin` (id or @username). The last admin can't be removed.
- Every new suggestion → a notification to all admins: type emoji + sender. Messages from one person within 15 s → one notification.
- Each notification starts with the global suggestion number(s) — #7, #13–16 — the same message always shows the same number.
- Short text (≤ 500 chars) → shown as is. Long → what was sent and its size, then a 1–3 sentence TLDR.
- Each notification shows a draft reply and quick buttons:
  - a reaction the model picked for this message (🔥, 🤔, 👍…) — puts it on the person's message.
  - ✨ Отправить черновик — sends the draft (written by the largest Groq model, gpt-oss-120b).
- Or answer yourself with text, voice, circle or video — sent as the same type; 👌 on your message when delivered:
  - reply to the notification, or
  - just send the next message: it goes to the person from your latest notification.
- Or do nothing — the person only sees 👌.
- A viewer's reaction on an admin's answer appears on that admin's own message (on the notification, if the answer was a sent draft), replacing 👌; removed → 👌 again. Reactions on other bot messages, or emoji Telegram won't let bots set → a short note to admins.
- `/original` as a reply → the person's original messages.
- `/summary` → all suggestions grouped by topic.
- `/version`, `/whoami` work for everyone but are shown only in the admin menu.
- An admin's own suggestions: only possible via a non-admin account (every non-command admin message is a reply to someone).

## Open

- «принимает только текст»: should the bot *say* it accepts only text, or *accept* only text for now?
- `/status` (count of suggestions and users) — in product.md, not built.
- Auto-start after reboot / restart on crash — today the bots run in tmux sessions `audience_topic_inbox_bot` (test) and `el_ping_bot` (prod) and stay down if they crash.
