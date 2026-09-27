import asyncio

from aiogram.methods import CopyMessage

from bot import texts
from bot.handlers import Limits, create_dispatcher
from tests.conftest import AUTHOR_ID, VIEWER_ID, FakeSpeech, FakeSummarizer, make_update

SECOND_ADMIN = 300


def _dp(storage, summarizer=None, burst_s=0.0):
    return create_dispatcher(storage=storage, speech=FakeSpeech(), summarizer=summarizer or FakeSummarizer(),
                             limits=Limits(burst_s=burst_s))


def _notification_id(session, admin_id):
    """message_id the mocked Bot API assigned to the last notification sent to admin_id."""
    from aiogram.methods import SendMessage
    sends = [(i, r) for i, r in enumerate(session.requests) if isinstance(r, SendMessage) and r.chat_id == admin_id]
    index, _ = sends[-1]
    return index + 1 + 1000  # MockedSession: message_id = len(requests) + 1000 at send time


# --- TLDR ---

async def test_short_message_shown_verbatim_without_tldr(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(text="видео про монтаж"))
    [note] = session.sent_to(AUTHOR_ID)
    assert "💬 [текст]" in note and "видео про монтаж" in note and "@ann" in note
    assert "TLDR" not in note and "📏" not in note


async def test_long_message_gets_llm_tldr(bot, session, storage):
    summarizer = FakeSummarizer()
    long = "очень длинный текст " * 200
    await _dp(storage, summarizer).feed_update(bot, make_update(text=long))
    [note] = session.sent_to(AUTHOR_ID)
    assert "КОРОТКО: хочет видео про ИИ" in note and long not in note
    assert summarizer.assist_calls == [long.strip()]


async def test_tldr_failure_falls_back_to_truncated_text(bot, session, storage):
    summarizer = FakeSummarizer(error=RuntimeError("groq down"))
    await _dp(storage, summarizer).feed_update(bot, make_update(text="слово " * 500))
    [note] = session.sent_to(AUTHOR_ID)
    assert "слово" in note and len(note) < 900
    assert await storage.count() == 1


async def test_burst_becomes_one_notification(bot, session, storage):
    summarizer = FakeSummarizer()
    dp = _dp(storage, summarizer, burst_s=0.1)
    for i, part in enumerate(["часть один " * 30, "часть два " * 30, "часть три " * 30]):
        await dp.feed_update(bot, make_update(text=part, message_id=40 + i))
    assert session.sent_to(AUTHOR_ID) == []  # still collecting
    await asyncio.sleep(0.3)
    [note] = session.sent_to(AUTHOR_ID)
    assert "[текст ×3]" in note
    assert len(summarizer.assist_calls) == 1 and "часть три" in summarizer.assist_calls[0]


async def test_separate_bursts_separate_notifications(bot, session, storage):
    dp = _dp(storage, burst_s=0.05)
    await dp.feed_update(bot, make_update(text="первое", message_id=1))
    await asyncio.sleep(0.2)
    await dp.feed_update(bot, make_update(text="второе", message_id=2))
    await asyncio.sleep(0.2)
    assert len(session.sent_to(AUTHOR_ID)) == 2


async def test_pending_burst_flushed_on_shutdown(bot, session, storage):
    dp = _dp(storage, burst_s=60)
    await dp.feed_update(bot, make_update(text="идея"))
    await dp["notifier"].close()
    assert len(session.sent_to(AUTHOR_ID)) == 1


# --- replies ---

async def test_admin_reply_relayed_anonymously(bot, session, storage):
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(text="идея", message_id=70))
    notif = _notification_id(session, AUTHOR_ID)
    await dp.feed_update(bot, make_update(user_id=AUTHOR_ID, text="спасибо, сделаем!", message_id=900, reply_to=notif))
    [copy] = session.copies()
    assert (copy.chat_id, copy.from_chat_id, copy.message_id) == (VIEWER_ID, AUTHOR_ID, 900)
    assert copy.reply_parameters.message_id == 70
    assert await storage.count() == 1  # the reply is not a suggestion
    assert len(session.reactions()) == 2  # 👍 on the suggestion, 👍 on the admin's reply


async def test_video_note_reply_relayed_as_copy(bot, session, storage):
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(text="идея", message_id=70))
    notif = _notification_id(session, AUTHOR_ID)
    await dp.feed_update(bot, make_update(user_id=AUTHOR_ID, video_note=True, message_id=901, reply_to=notif))
    [copy] = session.copies()
    assert copy.chat_id == VIEWER_ID and copy.message_id == 901
    assert await storage.count() == 1


async def test_reply_works_after_restart(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(text="идея", message_id=70))
    notif = _notification_id(session, AUTHOR_ID)
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text="ок", message_id=902, reply_to=notif))
    assert [c.chat_id for c in session.copies()] == [VIEWER_ID]


async def test_second_admin_can_reply_to_own_copy(bot, session, storage):
    await storage.add_admin(SECOND_ADMIN, "bob", added_by=AUTHOR_ID)
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(text="идея", message_id=70))
    notif = _notification_id(session, SECOND_ADMIN)
    await dp.feed_update(bot, make_update(user_id=SECOND_ADMIN, text="ответ", message_id=903, reply_to=notif))
    [copy] = session.copies()
    assert copy.chat_id == VIEWER_ID and copy.from_chat_id == SECOND_ADMIN


async def test_relay_failure_reported_to_admin(bot, session, storage):
    session.fail_copy = True
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(text="идея", message_id=70))
    notif = _notification_id(session, AUTHOR_ID)
    await dp.feed_update(bot, make_update(user_id=AUTHOR_ID, text="ответ", message_id=904, reply_to=notif))
    assert session.sent_to(AUTHOR_ID)[-1] == texts.RELAY_FAILED


async def test_original_on_demand(bot, session, storage):
    dp = _dp(storage, burst_s=0.05)
    await dp.feed_update(bot, make_update(text="раз", message_id=71))
    await dp.feed_update(bot, make_update(voice=True, message_id=72))
    await asyncio.sleep(0.2)
    notif = _notification_id(session, AUTHOR_ID)
    await dp.feed_update(bot, make_update(user_id=AUTHOR_ID, text="/original", message_id=905, reply_to=notif))
    assert [(c.chat_id, c.from_chat_id, c.message_id) for c in session.copies()] == [
        (AUTHOR_ID, VIEWER_ID, 71), (AUTHOR_ID, VIEWER_ID, 72)]


async def test_original_without_reply_shows_usage(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text="/original"))
    assert session.sent_to(AUTHOR_ID) == [texts.ORIGINAL_USAGE]


async def test_admin_message_with_nobody_to_answer(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text="моя идея", reply_to=12345))
    assert session.copies() == []
    assert await storage.count() == 0
    assert session.sent_to(AUTHOR_ID) == [texts.ADMIN_NO_TARGET]


async def test_non_admin_reply_is_a_suggestion_not_relayed(bot, session, storage):
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(text="идея", message_id=70))
    notif = _notification_id(session, AUTHOR_ID)
    await dp.feed_update(bot, make_update(user_id=VIEWER_ID, text="ещё", message_id=71, reply_to=notif))
    assert session.copies() == []
    assert await storage.count() == 2


async def test_model_tldr_prefix_not_doubled(bot, session, storage):
    from bot.summary import Assist
    summarizer = FakeSummarizer()
    summarizer.assist_result = Assist(tldr="TLDR: книга про ясность", emoji="🔥", reply="")
    await _dp(storage, summarizer).feed_update(bot, make_update(text="длинно " * 200))
    [note] = session.sent_to(AUTHOR_ID)
    assert "TLDR: книга про ясность" in note and "TLDR: TLDR" not in note


# --- notifications sent before the reply feature (no stored mapping) ---

def _legacy(sender="@ann (id 100)", text="хочу видео про монтаж"):
    return f"🆕 Новое предложение 💬 от {sender}:\n\n{text}"


async def test_reply_to_legacy_notification_relayed(bot, session, storage):
    await storage.add(user_id=VIEWER_ID, username="ann", kind="text", text="хочу видео про монтаж",
                      chat_id=VIEWER_ID, message_id=33)
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text="сделаем", message_id=906,
                                                    reply_to=555, reply_text=_legacy()))
    [copy] = session.copies()
    assert (copy.chat_id, copy.from_chat_id, copy.message_id) == (VIEWER_ID, AUTHOR_ID, 906)
    assert copy.reply_parameters.message_id == 33
    assert await storage.count() == 1


async def test_legacy_original_found_by_text(bot, session, storage):
    await storage.add(user_id=VIEWER_ID, username="ann", kind="text", text="хочу видео про монтаж",
                      chat_id=VIEWER_ID, message_id=33)
    await storage.add(user_id=VIEWER_ID, username="ann", kind="text", text="другое", chat_id=VIEWER_ID, message_id=34)
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text="/original", reply_to=555,
                                                    reply_text=_legacy()))
    assert [(c.chat_id, c.from_chat_id, c.message_id) for c in session.copies()] == [(AUTHOR_ID, VIEWER_ID, 33)]


async def test_legacy_without_known_text_still_relays_without_quote(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text="ок", message_id=907,
                                                    reply_to=555, reply_text=_legacy()))
    [copy] = session.copies()
    assert copy.chat_id == VIEWER_ID and copy.reply_parameters is None


async def test_legacy_spoofed_full_name_uses_real_id(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text="ок", reply_to=555,
                                                    reply_text=_legacy(sender="Evil (id 5) (id 100)")))
    assert [c.chat_id for c in session.copies()] == [VIEWER_ID]


async def test_legacy_looking_message_not_from_bot_is_ignored(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text="ок", reply_to=555,
                                                    reply_text=_legacy(), reply_from_bot=False))
    assert session.copies() == []  # not trusted as a legacy notification, and no other notification exists
    assert session.sent_to(AUTHOR_ID) == [texts.ADMIN_NO_TARGET]
