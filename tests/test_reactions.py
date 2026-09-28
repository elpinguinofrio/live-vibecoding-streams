from aiogram.methods import SendMessage

from bot import texts
from bot.handlers import Limits, create_dispatcher
from tests.conftest import AUTHOR_ID, VIEWER_ID, FakeSpeech, FakeSummarizer, make_reaction, make_update

SECOND_ADMIN = 300


def _dp(storage):
    return create_dispatcher(storage=storage, speech=FakeSpeech(), summarizer=FakeSummarizer(),
                             limits=Limits(burst_s=0))


def _sends(session, chat_id):
    return [(i, m) for i, m in enumerate(session.requests) if isinstance(m, SendMessage) and m.chat_id == chat_id]


async def test_viewer_reaction_on_admin_answer_reaches_every_admin_under_their_notification(bot, session, storage):
    await storage.add_admin(SECOND_ADMIN, "bob", added_by=AUTHOR_ID)
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(text="идея", message_id=70))
    notes = {a: _sends(session, a)[-1][0] + 1 + 1000 for a in (AUTHOR_ID, SECOND_ADMIN)}
    await dp.feed_update(bot, make_update(user_id=AUTHOR_ID, text="спасибо, сделаем", message_id=900,
                                          reply_to=notes[AUTHOR_ID]))
    [copy] = session.copies()
    copied_id = session.requests.index(copy) + 1 + 5000
    await dp.feed_update(bot, make_reaction(VIEWER_ID, copied_id, new=["😁"]))
    for admin, note_id in notes.items():
        _, msg = _sends(session, admin)[-1]
        assert "😁" in msg.text and "@ann" in msg.text and "спасибо, сделаем" in msg.text
        assert msg.reply_parameters.message_id == note_id


async def test_reaction_on_other_bot_message_still_reported(bot, session, storage):
    await _dp(storage).feed_update(bot, make_reaction(VIEWER_ID, 12345, new=["❤"]))
    [msg] = session.sent_to(AUTHOR_ID)
    assert "❤" in msg and "@ann" in msg


async def test_removed_reaction_reported(bot, session, storage):
    await _dp(storage).feed_update(bot, make_reaction(VIEWER_ID, 12345, new=[], old=["😁"]))
    [msg] = session.sent_to(AUTHOR_ID)
    assert "убрал" in msg and "😁" in msg


async def test_admin_own_reactions_ignored(bot, session, storage):
    await _dp(storage).feed_update(bot, make_reaction(AUTHOR_ID, 5, new=["👍"]))
    assert session.sent_texts() == []


async def test_welcome_has_no_reaction_promise():
    assert "поставлю" not in texts.WELCOME
