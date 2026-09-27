from bot import texts
from bot.handlers import create_dispatcher
from tests.conftest import AUTHOR_ID, VIEWER_ID, FakeSpeech, FakeSummarizer, make_update


def _dp(storage, summarizer):
    return create_dispatcher(storage=storage, speech=FakeSpeech(), summarizer=summarizer)


async def _seed(storage):
    await storage.add(user_id=VIEWER_ID, username="ann", kind="text", text="про ИИ", chat_id=VIEWER_ID, message_id=1)
    await storage.add(user_id=VIEWER_ID, username="ann", kind="voice", text="про роботов", chat_id=VIEWER_ID, message_id=2)


async def test_author_gets_summary(bot, session, storage):
    await _seed(storage)
    summarizer = FakeSummarizer(result="Темы: ИИ (2)")
    await _dp(storage, summarizer).feed_update(bot, make_update(user_id=AUTHOR_ID, text="/summary"))
    assert summarizer.calls == [["про ИИ", "про роботов"]]
    assert session.sent_texts() == ["Темы: ИИ (2)"]
    assert await storage.count() == 2  # command not stored


async def test_long_summary_split(bot, session, storage):
    await _seed(storage)
    long = "\n".join(["тема " + "z" * 90] * 100)
    await _dp(storage, FakeSummarizer(result=long)).feed_update(bot, make_update(user_id=AUTHOR_ID, text="/summary"))
    sent = session.sent_texts()
    assert len(sent) > 1 and all(len(s) <= 4096 for s in sent)
    assert "\n".join(sent) == long


async def test_empty_summary(bot, session, storage):
    summarizer = FakeSummarizer()
    await _dp(storage, summarizer).feed_update(bot, make_update(user_id=AUTHOR_ID, text="/summary"))
    assert summarizer.calls == []
    assert session.sent_texts() == [texts.NO_SUGGESTIONS]


async def test_non_author_refused(bot, session, storage):
    await _seed(storage)
    summarizer = FakeSummarizer()
    await _dp(storage, summarizer).feed_update(bot, make_update(user_id=VIEWER_ID, text="/summary"))
    assert summarizer.calls == []
    assert session.sent_texts() == [texts.NOT_ADMIN]


async def test_summary_error(bot, session, storage):
    await _seed(storage)
    await _dp(storage, FakeSummarizer(error=RuntimeError("down"))).feed_update(bot, make_update(user_id=AUTHOR_ID, text="/summary"))
    assert session.sent_texts() == [texts.SUMMARY_FAILED]


async def test_summary_refused_when_author_not_configured(bot, session, storage):
    await _seed(storage)
    summarizer = FakeSummarizer()
    await storage._conn().execute("DELETE FROM admins")
    dp = create_dispatcher(storage=storage, speech=FakeSpeech(), summarizer=summarizer)
    await dp.feed_update(bot, make_update(user_id=AUTHOR_ID, text="/summary"))
    assert summarizer.calls == []
    assert session.sent_texts() == [texts.NOT_ADMIN]


from bot.handlers import Limits


async def test_summary_db_read_failure(bot, session, storage):
    await _seed(storage)
    await storage.close()
    await _dp(storage, FakeSummarizer()).feed_update(bot, make_update(user_id=AUTHOR_ID, text="/summary"))
    assert session.sent_texts() == [texts.SUMMARY_FAILED]


async def test_summary_timeout(bot, session, storage):
    await _seed(storage)
    dp = create_dispatcher(storage=storage, speech=FakeSpeech(), summarizer=FakeSummarizer(delay=1), limits=Limits(summary_timeout_s=0.05))
    await dp.feed_update(bot, make_update(user_id=AUTHOR_ID, text="/summary"))
    assert session.sent_texts() == [texts.SUMMARY_TIMEOUT]


async def test_summary_partial_send_failure_does_not_raise(bot, session, storage):
    await _seed(storage)
    session.fail_send_after = 1
    long = "\n".join(["тема " + "z" * 90] * 100)
    await _dp(storage, FakeSummarizer(result=long)).feed_update(bot, make_update(user_id=AUTHOR_ID, text="/summary"))
    assert len(session.sent_texts()) == 2  # first chunk ok, second failed, then stopped
