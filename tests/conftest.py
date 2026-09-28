import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (AnswerCallbackQuery, CopyMessage, DeleteMyCommands, EditMessageReplyMarkup, GetFile,
                             SendMessage, SetMessageReaction, SetMyCommands, TelegramMethod)
from aiogram.types import CallbackQuery, Chat, Document, MessageReactionUpdated, ReactionTypeEmoji, File, Message, MessageId, PhotoSize, Update, User, Video, VideoNote, Voice

from bot.storage import Storage
from bot.summary import Assist

AUTHOR_ID = 1
VIEWER_ID = 100


class MockedSession(BaseSession):
    """Records Bot API calls instead of hitting Telegram."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self.file_bytes = b"OggS-fake-voice"
        self.fail_reaction = False
        self.fail_send_after: int | None = None  # number of successful SendMessage calls before failing
        self.on_reaction = None  # async callable(SetMessageReaction) run before the reaction "succeeds"
        self.fail_copy = False

    async def close(self) -> None:
        pass

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        self.requests.append(method)
        if isinstance(method, SendMessage):
            if self.fail_send_after is not None and len(self.calls(SendMessage)) > self.fail_send_after:
                raise TelegramBadRequest(method=method, message="Bad Request: send failed")
            return Message(
                message_id=len(self.requests) + 1000,
                date=datetime.now(timezone.utc),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        if isinstance(method, CopyMessage):
            if self.fail_copy:
                raise TelegramBadRequest(method=method, message="Forbidden: bot was blocked by the user")
            return MessageId(message_id=len(self.requests) + 5000)
        if isinstance(method, (SetMyCommands, DeleteMyCommands, AnswerCallbackQuery, EditMessageReplyMarkup)):
            return True
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_path="voice/file.oga")
        if isinstance(method, SetMessageReaction):
            if self.on_reaction is not None:
                await self.on_reaction(method)
            if self.fail_reaction:
                raise TelegramBadRequest(method=method, message="Bad Request: REACTION_INVALID")
            return True
        raise AssertionError(f"unexpected Bot API call: {type(method).__name__}")

    async def stream_content(self, url: str, headers: dict[str, Any] | None = None, timeout: int = 30,
                             chunk_size: int = 65536, raise_for_status: bool = True) -> AsyncGenerator[bytes, None]:
        yield self.file_bytes

    def calls(self, kind: type) -> list[Any]:
        return [r for r in self.requests if isinstance(r, kind)]

    def reactions(self) -> list[SetMessageReaction]:
        return self.calls(SetMessageReaction)

    def sent_texts(self) -> list[str]:
        return [m.text for m in self.calls(SendMessage)]

    def copies(self) -> list[CopyMessage]:
        return self.calls(CopyMessage)

    def sent_to(self, chat_id: int) -> list[str]:
        return [m.text for m in self.calls(SendMessage) if m.chat_id == chat_id]


class FakeSpeech:
    model = "fake-stt-model"

    def __init__(self, result: str = "расскажи про нейросети", error: Exception | None = None,
                 delay: float = 0.0, fail_times: int = 0) -> None:
        self.result, self.error, self.delay, self.calls = result, error, delay, []
        self.fail_times = fail_times  # the first N calls raise, later ones succeed
        self.active = self.max_active = 0

    async def transcribe(self, data: bytes, filename: str) -> str:
        self.calls.append((data, filename))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            if len(self.calls) <= self.fail_times:
                raise RuntimeError("stt temporarily down")
            return self.result
        finally:
            self.active -= 1


class FakeSummarizer:
    def __init__(self, result: str = "Темы: ИИ (2)", error: Exception | None = None, delay: float = 0.0) -> None:
        self.result, self.error, self.delay, self.calls = result, error, delay, []
        self.assist_result = Assist(tldr="КОРОТКО: хочет видео про ИИ", emoji="🔥", reply="Спасибо, учту!")
        self.assist_calls = []

    async def assist(self, text: str) -> Assist:
        self.assist_calls.append(text)
        if self.error:
            raise self.error
        return self.assist_result

    async def summarize(self, texts: list[str]) -> str:
        self.calls.append(texts)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


_update_id = 0


def make_update(user_id: int = VIEWER_ID, chat_type: str = "private", text: str | None = None,
                voice: bool = False, sticker: bool = False, message_id: int = 10,
                voice_size: int | None = None, duration: int = 3, video_note: bool = False, video: bool = False,
                photo: bool = False, document: bool = False, caption: str | None = None,
                reply_to: int | None = None, reply_text: str = "🆕 …", reply_from_bot: bool = True) -> Update:
    global _update_id
    _update_id += 1
    extra: dict[str, Any] = {}
    if text is not None:
        extra["text"] = text
    if voice:
        extra["voice"] = Voice(file_id="voice-1", file_unique_id="vu-1", duration=duration, file_size=voice_size)
    if sticker:
        from aiogram.types import Sticker
        extra["sticker"] = Sticker(file_id="s", file_unique_id="s", type="regular", width=1, height=1,
                                   is_animated=False, is_video=False)
    if video_note:
        extra["video_note"] = VideoNote(file_id="vn-1", file_unique_id="vnu-1", length=240, duration=5)
    if video:
        extra["video"] = Video(file_id="vid-1", file_unique_id="vidu-1", width=1, height=1, duration=5,
                               file_size=voice_size)
    if photo:
        extra["photo"] = [PhotoSize(file_id="ph-small", file_unique_id="phs", width=90, height=90),
                          PhotoSize(file_id="ph-1", file_unique_id="phu-1", width=800, height=800)]
    if document:
        extra["document"] = Document(file_id="doc-1", file_unique_id="docu-1", file_name="a.pdf")
    if caption is not None:
        extra["caption"] = caption
    chat_id = user_id if chat_type == "private" else -500
    if reply_to is not None:
        extra["reply_to_message"] = Message(message_id=reply_to, date=datetime.now(timezone.utc),
                                            chat=Chat(id=chat_id, type=chat_type),
                                            from_user=User(id=42 if reply_from_bot else 7, is_bot=reply_from_bot,
                                                           first_name="Bot"), text=reply_text)
    return Update(
        update_id=_update_id,
        message=Message(
            message_id=message_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type=chat_type),
            from_user=User(id=user_id, is_bot=False, first_name="Ann", username="ann"),
            **extra,
        ),
    )


def make_callback(user_id: int, message_id: int, data: str) -> Update:
    """An admin pressing an inline button under the bot's message message_id in their private chat."""
    global _update_id
    _update_id += 1
    return Update(update_id=_update_id, callback_query=CallbackQuery(
        id=f"cb{_update_id}", from_user=User(id=user_id, is_bot=False, first_name="Admin"), chat_instance="ci",
        data=data, message=Message(message_id=message_id, date=datetime.now(timezone.utc),
                                   chat=Chat(id=user_id, type="private"),
                                   from_user=User(id=42, is_bot=True, first_name="Bot"), text="🆕 …")))


def make_reaction(user_id: int, message_id: int, new: list[str], old: list[str] | None = None) -> Update:
    """A viewer changing their reaction on message message_id in their private chat with the bot."""
    global _update_id
    _update_id += 1
    return Update(update_id=_update_id, message_reaction=MessageReactionUpdated(
        chat=Chat(id=user_id, type="private"), message_id=message_id, date=datetime.now(timezone.utc),
        user=User(id=user_id, is_bot=False, first_name="Ann", username="ann"),
        old_reaction=[ReactionTypeEmoji(emoji=e) for e in (old or [])],
        new_reaction=[ReactionTypeEmoji(emoji=e) for e in new]))


@pytest.fixture
async def storage(tmp_path) -> AsyncGenerator[Storage, None]:
    s = Storage(str(tmp_path / "test.db"))
    await s.open()
    await s.seed_admins([AUTHOR_ID])
    yield s
    await s.close()


@pytest.fixture
def session() -> MockedSession:
    return MockedSession()


@pytest.fixture
def bot(session: MockedSession) -> Bot:
    return Bot(token="42:TEST", session=session)
