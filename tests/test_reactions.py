from aiogram.methods import SendMessage

from bot import texts
from bot.handlers import Limits, create_dispatcher
from tests.conftest import AUTHOR_ID, VIEWER_ID, FakeSpeech, FakeSummarizer, make_reaction, make_update

def _dp(storage):
    return create_dispatcher(storage=storage, speech=FakeSpeech(), summarizer=FakeSummarizer(),
                             limits=Limits(burst_s=0))


def _sends(session, chat_id):
    return [(i, m) for i, m in enumerate(session.requests) if isinstance(m, SendMessage) and m.chat_id == chat_id]


async def _answer_and_copy(dp, bot, session):
    """Viewer suggests, admin answers it; returns the id of the bot's copy in the viewer chat."""
    await dp.feed_update(bot, make_update(text="идея", message_id=70))
    note_id = _sends(session, AUTHOR_ID)[-1][0] + 1 + 1000
    await dp.feed_update(bot, make_update(user_id=AUTHOR_ID, text="спасибо, сделаем", message_id=900,
                                          reply_to=note_id))
    copy = session.copies()[-1]
    return session.requests.index(copy) + 1 + 5000, note_id


async def test_viewer_reaction_mirrored_on_admins_own_answer(bot, session, storage):
    dp = _dp(storage)
    copied_id, _ = await _answer_and_copy(dp, bot, session)
    sends_before = len(session.calls(SendMessage))
    await dp.feed_update(bot, make_reaction(VIEWER_ID, copied_id, new=["😁"]))
    mirror = session.reactions()[-1]
    assert (mirror.chat_id, mirror.message_id, mirror.reaction[0].emoji) == (AUTHOR_ID, 900, "😁")
    assert len(session.calls(SendMessage)) == sends_before  # no extra message


async def test_removed_reaction_puts_ok_hand_back(bot, session, storage):
    dp = _dp(storage)
    copied_id, _ = await _answer_and_copy(dp, bot, session)
    await dp.feed_update(bot, make_reaction(VIEWER_ID, copied_id, new=[], old=["😁"]))
    mirror = session.reactions()[-1]
    assert (mirror.chat_id, mirror.message_id, mirror.reaction[0].emoji) == (AUTHOR_ID, 900, "👌")


async def test_reaction_on_sent_draft_mirrored_on_notification(bot, session, storage):
    from tests.conftest import make_callback
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(text="идея", message_id=70))
    note_id = _sends(session, AUTHOR_ID)[-1][0] + 1 + 1000
    await dp.feed_update(bot, make_callback(AUTHOR_ID, note_id, "draft"))
    draft_id = _sends(session, VIEWER_ID)[-1][0] + 1 + 1000
    await dp.feed_update(bot, make_reaction(VIEWER_ID, draft_id, new=["🔥"]))
    mirror = session.reactions()[-1]
    assert (mirror.chat_id, mirror.message_id, mirror.reaction[0].emoji) == (AUTHOR_ID, note_id, "🔥")


async def test_refused_emoji_falls_back_to_message_under_notification(bot, session, storage):
    dp = _dp(storage)
    copied_id, note_id = await _answer_and_copy(dp, bot, session)
    session.fail_reaction = True
    await dp.feed_update(bot, make_reaction(VIEWER_ID, copied_id, new=["🦖"]))
    _, msg = _sends(session, AUTHOR_ID)[-1]
    assert "🦖" in msg.text and "спасибо, сделаем" in msg.text
    assert msg.reply_parameters.message_id == 900


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
