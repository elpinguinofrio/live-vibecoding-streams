from aiogram.types import ReactionTypeEmoji

from bot import texts
from bot.handlers import create_dispatcher
from tests.conftest import AUTHOR_ID, VIEWER_ID, FakeSpeech, FakeSummarizer, make_update


def _dp(storage):
    return create_dispatcher(storage=storage, speech=FakeSpeech(), summarizer=FakeSummarizer())


async def test_text_saved_then_thumbs_up(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(text="Сделай видео про нейросети", message_id=42))
    items = await storage.list_all()
    assert [(i.kind, i.text, i.message_id, i.username) for i in items] == [("text", "Сделай видео про нейросети", 42, "ann")]
    [reaction] = session.reactions()
    assert reaction.message_id == 42
    assert reaction.reaction == [ReactionTypeEmoji(emoji="👌")]


async def test_save_failure_retried_silently(bot, session, storage):
    from bot.handlers import Limits

    await storage.close()  # force DB error
    dp = create_dispatcher(storage=storage, speech=FakeSpeech(), summarizer=FakeSummarizer(),
                           limits=Limits(burst_s=0, stt_retry_s=0.05))
    await dp.feed_update(bot, make_update(text="идея", message_id=4))
    assert session.reactions() == [] and session.sent_texts() == []
    await storage.open()  # database is back
    await dp["intake"].drain()
    assert [i.text for i in await storage.list_all()] == ["идея"]
    assert [r.message_id for r in session.reactions()] == [4]
    assert session.sent_to(VIEWER_ID) == []


async def test_group_messages_ignored(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(chat_type="group", text="идея"))
    assert await storage.count() == 0
    assert session.requests == []


async def test_start_not_saved(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(text="/start"))
    assert await storage.count() == 0
    assert session.sent_texts() == [texts.WELCOME]


async def test_whoami_replies_id(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(user_id=555, text="/whoami"))
    assert await storage.count() == 0
    assert session.sent_texts() == [texts.WHOAMI.format(user_id=555)]


async def test_sticker_gets_hint(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(sticker=True))
    assert await storage.count() == 0
    assert session.reactions() == []
    assert session.sent_texts() == [texts.UNSUPPORTED]


async def test_reaction_only_after_row_committed(bot, session, storage):
    seen = []

    async def check(method):
        seen.append(await storage.count())

    session.on_reaction = check
    await _dp(storage).feed_update(bot, make_update(text="идея"))
    assert seen == [1]


async def test_reaction_failure_falls_back_to_text(bot, session, storage):
    session.fail_reaction = True
    await _dp(storage).feed_update(bot, make_update(text="идея"))
    assert await storage.count() == 1
    assert session.sent_to(VIEWER_ID) == [texts.SAVED_FALLBACK]


async def test_reaction_and_fallback_failure_does_not_raise(bot, session, storage):
    session.fail_reaction = True
    session.fail_send_after = 0
    await _dp(storage).feed_update(bot, make_update(text="идея"))
    assert await storage.count() == 1


async def test_duplicate_update_stored_once_acked_twice(bot, session, storage):
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(text="идея", message_id=5))
    await dp.feed_update(bot, make_update(text="идея", message_id=5))
    assert await storage.count() == 1
    assert len(session.reactions()) == 2
