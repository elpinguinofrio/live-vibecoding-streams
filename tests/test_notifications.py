from aiogram.methods import SendMessage, SetMessageReaction

from bot import texts
from bot.handlers import Limits, create_dispatcher
from bot.summary import utf16_len
from tests.conftest import AUTHOR_ID, VIEWER_ID, FakeSpeech, FakeSummarizer, make_update


def _dp(storage, speech=None):
    return create_dispatcher(storage=storage, speech=speech or FakeSpeech(), summarizer=FakeSummarizer(),
                             limits=Limits(burst_s=0))


async def test_text_suggestion_notifies_author_after_reaction(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(text="видео про монтаж"))
    [note] = session.sent_to(AUTHOR_ID)
    assert "видео про монтаж" in note and "@ann" in note and "💬" in note
    kinds = [type(r).__name__ for r in session.requests]
    assert kinds.index("SetMessageReaction") < kinds.index("SendMessage")


async def test_voice_suggestion_notifies_author_with_transcript(bot, session, storage):
    await _dp(storage, FakeSpeech(result="расскажи про роботов")).feed_update(bot, make_update(voice=True))
    [note] = session.sent_to(AUTHOR_ID)
    assert "расскажи про роботов" in note and "🎙" in note


async def test_duplicate_delivery_notifies_once(bot, session, storage):
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(text="идея", message_id=3))
    await dp.feed_update(bot, make_update(text="идея", message_id=3))
    assert len(session.sent_to(AUTHOR_ID)) == 1


async def test_failed_save_does_not_notify(bot, session, storage):
    await storage.close()
    await _dp(storage).feed_update(bot, make_update(text="идея"))
    assert session.sent_to(AUTHOR_ID) == []
    assert session.sent_to(VIEWER_ID) == []
    await _dp(storage)["intake"].close()


async def test_no_author_configured_no_notification(bot, session, storage):
    await storage._conn().execute("DELETE FROM admins")
    await _dp(storage).feed_update(bot, make_update(text="идея"))
    assert session.calls(SendMessage) == []
    assert len(session.calls(SetMessageReaction)) == 1


async def test_notification_failure_keeps_reaction_and_does_not_raise(bot, session, storage):
    session.fail_send_after = 0
    await _dp(storage).feed_update(bot, make_update(text="идея"))
    assert len(session.reactions()) == 1
    assert await storage.count() == 1


async def test_long_suggestion_notification_fits_telegram_limit(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(text="😀" * 4000))
    [note] = session.sent_to(AUTHOR_ID)
    assert utf16_len(note) <= 4096
