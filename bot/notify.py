import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import texts
from bot.storage import Storage
from bot.summary import DEFAULT_REACTION, Assist, truncate_utf16, utf16_len

log = logging.getLogger(__name__)

KIND_LABELS = {
    "text": ("💬", "текст"),
    "voice": ("🎙", "голосовое"),
    "video_note": ("⭕", "кружок"),
    "video": ("🎬", "видео"),
    "photo": ("🖼", "фото"),
}
VERBATIM_LIMIT = 500  # shorter bursts are shown as-is, longer ones get an LLM TLDR (~2 tweets)


class Assistant(Protocol):
    async def assist(self, text: str) -> Assist: ...


def keyboard(emoji: str | None, draft: str | None) -> InlineKeyboardMarkup | None:
    """Quick actions under a notification: the model's reaction, and sending its draft reply."""
    row = []
    if emoji:
        row.append(InlineKeyboardButton(text=emoji, callback_data="react"))
    if draft:
        row.append(InlineKeyboardButton(text=texts.DRAFT_BUTTON, callback_data="draft"))
    return InlineKeyboardMarkup(inline_keyboard=[row]) if row else None


@dataclass
class _Burst:
    bot: Bot
    chat_id: int
    sender: str
    kinds: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    message_ids: list[int] = field(default_factory=list)
    durations: list[int] = field(default_factory=list)
    timer: asyncio.Task | None = None


def _types_label(kinds: list[str]) -> str:
    counts = Counter(kinds)
    parts = []
    for kind in dict.fromkeys(kinds):
        icon, name = KIND_LABELS[kind]
        parts.append(f"{icon} [{name} ×{counts[kind]}]" if counts[kind] > 1 else f"{icon} [{name}]")
    return " ".join(parts)


def _size_line(burst: _Burst) -> str:
    """What was sent and how big, shown above the TLDR of a long burst: 📏 ~12 200 символов, 🎙 2:30."""
    parts = []
    chars = sum(utf16_len(t) for k, t in zip(burst.kinds, burst.texts) if k == "text")
    if chars:
        parts.append(f"~{chars:,} символов".replace(",", " "))
    for kind in dict.fromkeys(k for k in burst.kinds if k in ("voice", "video_note", "video")):
        seconds = sum(d for k, d in zip(burst.kinds, burst.durations) if k == kind and d)
        if seconds:
            parts.append(f"{KIND_LABELS[kind][0]} {seconds // 60}:{seconds % 60:02d}")
    return f"📏 {', '.join(parts)}\n" if parts else ""


class Notifier:
    """Tells every admin about new suggestions: one TLDR per burst of messages from the same person."""

    def __init__(self, storage: Storage, summarizer: Assistant, *, burst_s: float, tldr_timeout_s: float) -> None:
        self._storage = storage
        self._summarizer = summarizer
        self._burst_s = burst_s
        self._tldr_timeout_s = tldr_timeout_s
        self._bursts: dict[int, _Burst] = {}
        self._flushing: set[asyncio.Task] = set()

    async def add(self, bot: Bot, *, chat_id: int, sender: str, kind: str, text: str, message_id: int,
                  duration: int | None = None) -> None:
        burst = self._bursts.setdefault(chat_id, _Burst(bot, chat_id, sender))
        burst.kinds.append(kind)
        burst.texts.append(text.strip())
        burst.message_ids.append(message_id)
        burst.durations.append(duration or 0)
        if self._burst_s <= 0:
            await self._send(self._bursts.pop(chat_id))
            return
        if burst.timer is not None:
            burst.timer.cancel()  # still sleeping: the burst was not taken yet
        burst.timer = asyncio.create_task(self._send_later(chat_id))
        self._flushing.add(burst.timer)
        burst.timer.add_done_callback(self._flushing.discard)

    async def _send_later(self, chat_id: int) -> None:
        await asyncio.sleep(self._burst_s)
        burst = self._bursts.pop(chat_id)  # before any await: later messages start a new burst
        burst.timer = None
        await self._send(burst)

    async def close(self) -> None:
        """Send whatever is still being collected (shutdown) and wait for in-flight notifications."""
        for chat_id in list(self._bursts):
            burst = self._bursts.pop(chat_id)
            if burst.timer is not None:
                burst.timer.cancel()
            await self._send(burst)
        if self._flushing:
            await asyncio.gather(*self._flushing, return_exceptions=True)

    async def _assist(self, combined: str) -> Assist | None:
        try:
            async with asyncio.timeout(self._tldr_timeout_s):
                return await self._summarizer.assist(combined)
        except Exception as exc:  # the notification still goes out, just without the digest and draft
            log.error("assist failed: %s", type(exc).__name__)
            return None

    async def _send(self, burst: _Burst) -> None:
        try:
            combined = "\n\n".join(burst.texts)
            assist = await self._assist(combined)
            if utf16_len(combined) <= VERBATIM_LIMIT:
                body = combined
            else:
                tldr = assist.tldr.removeprefix("TLDR:").strip() if assist else ""
                body = _size_line(burst) + (f"TLDR: {tldr}" if tldr else truncate_utf16(combined, VERBATIM_LIMIT))
            emoji = (assist.emoji if assist else "") or DEFAULT_REACTION
            draft = (assist.reply if assist else "") or None
            note = texts.NEW_SUGGESTION.format(types=_types_label(burst.kinds), sender=burst.sender, body=body,
                                               draft=texts.DRAFT.format(reply=draft) if draft else "")
            admin_ids = await self._storage.admin_ids()
        except Exception as exc:
            log.error("failed to prepare notification: %s", type(exc).__name__)
            return
        for admin_id in admin_ids:
            try:
                sent = await burst.bot.send_message(admin_id, note, reply_markup=keyboard(emoji, draft))
                await self._storage.add_notification(admin_chat_id=admin_id, message_id=sent.message_id,
                                                     user_chat_id=burst.chat_id,
                                                     source_message_ids=burst.message_ids, emoji=emoji, draft=draft)
            except Exception as exc:
                log.error("failed to notify admin: %s", type(exc).__name__)
