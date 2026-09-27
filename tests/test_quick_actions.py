from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, SendMessage
from aiogram.types import ReactionTypeEmoji

from bot import texts
from bot.handlers import Limits, create_dispatcher
from tests.conftest import AUTHOR_ID, VIEWER_ID, FakeSpeech, FakeSummarizer, make_callback, make_update


def _dp(storage, summarizer=None):
    return create_dispatcher(storage=storage, speech=FakeSpeech(), summarizer=summarizer or FakeSummarizer(),
                             limits=Limits(burst_s=0))


def _last_note(session, admin_id=AUTHOR_ID):
    """(message_id assigned by the mock, SendMessage) of the last notification sent to admin_id."""
    sends = [(i, r) for i, r in enumerate(session.requests) if isinstance(r, SendMessage) and r.chat_id == admin_id]
    index, method = sends[-1]
    return index + 1 + 1000, method


async def test_viewer_gets_ok_hand_not_thumbs_up(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(text="идея", message_id=8))
    [reaction] = session.reactions()
    assert (reaction.chat_id, reaction.message_id, reaction.reaction) == (VIEWER_ID, 8, [ReactionTypeEmoji(emoji="👌")])


async def test_notification_has_model_emoji_and_draft_buttons(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(text="идея"))
    _, note = _last_note(session)
    [row] = note.reply_markup.inline_keyboard
    assert [(b.text, b.callback_data) for b in row] == [("🔥", "react"), ("✨ Отправить черновик", "draft")]
    assert "Спасибо, учту!" in note.text


async def test_llm_down_notification_still_sent_with_default_reaction_only(bot, session, storage):
    await _dp(storage, FakeSummarizer(error=RuntimeError("down"))).feed_update(bot, make_update(text="идея"))
    _, note = _last_note(session)
    [row] = note.reply_markup.inline_keyboard
    assert [b.callback_data for b in row] == ["react"] and row[0].text == "👍"
    assert "Черновик" not in note.text


async def test_react_button_puts_suggested_emoji_on_viewer_message(bot, session, storage):
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(text="идея", message_id=21))
    note_id, _ = _last_note(session)
    await dp.feed_update(bot, make_callback(AUTHOR_ID, note_id, "react"))
    reaction = session.reactions()[-1]
    assert (reaction.chat_id, reaction.message_id, reaction.reaction) == (VIEWER_ID, 21, [ReactionTypeEmoji(emoji="🔥")])
    assert len(session.calls(AnswerCallbackQuery)) == 1
    [edit] = session.calls(EditMessageReplyMarkup)
    assert [b.callback_data for b in edit.reply_markup.inline_keyboard[0]] == ["draft"]  # pressed button gone


async def test_draft_button_sends_draft_to_viewer(bot, session, storage):
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(text="идея", message_id=22))
    note_id, _ = _last_note(session)
    await dp.feed_update(bot, make_callback(AUTHOR_ID, note_id, "draft"))
    assert session.sent_to(VIEWER_ID) == ["Спасибо, учту!"]
    sent = [m for m in session.calls(SendMessage) if m.chat_id == VIEWER_ID][0]
    assert sent.reply_parameters.message_id == 22


async def test_non_admin_cannot_press_buttons(bot, session, storage):
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(text="идея"))
    note_id, _ = _last_note(session)
    await dp.feed_update(bot, make_callback(VIEWER_ID, note_id, "draft"))
    assert session.sent_to(VIEWER_ID) == []
    [answer] = session.calls(AnswerCallbackQuery)
    assert answer.text == texts.NOT_ADMIN


async def test_admin_next_message_goes_to_latest_notification(bot, session, storage):
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(user_id=VIEWER_ID, text="первый", message_id=30))
    await dp.feed_update(bot, make_update(user_id=555, text="второй", message_id=31))
    await dp.feed_update(bot, make_update(user_id=AUTHOR_ID, voice=True, message_id=910))  # no reply: next message
    [copy] = session.copies()
    assert (copy.chat_id, copy.message_id, copy.reply_parameters.message_id) == (555, 910, 31)
    assert session.reactions()[-1].reaction == [ReactionTypeEmoji(emoji="👌")]  # delivered
    assert await storage.count() == 2  # admin's message is an answer, not a suggestion


async def test_long_text_gets_size_line_before_tldr(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(text="слово " * 400))
    _, note = _last_note(session)
    assert "📏" in note.text and "символов" in note.text
    assert note.text.index("📏") < note.text.index("TLDR:")


async def test_long_voice_size_line_shows_duration(bot, session, storage):
    dp = create_dispatcher(storage=storage, speech=FakeSpeech(result="длинная речь " * 60),
                           summarizer=FakeSummarizer(), limits=Limits(burst_s=0))
    await dp.feed_update(bot, make_update(voice=True, duration=150))
    _, note = _last_note(session)
    assert "2:30" in note.text
