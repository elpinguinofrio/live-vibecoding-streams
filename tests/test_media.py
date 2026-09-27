from pathlib import Path

from aiogram.methods import GetFile

from bot import texts
from bot.handlers import Limits, create_dispatcher
from tests.conftest import AUTHOR_ID, VIEWER_ID, FakeSpeech, FakeSummarizer, make_update


def _dp(storage, speech=None):
    return create_dispatcher(storage=storage, speech=speech or FakeSpeech(), summarizer=FakeSummarizer(),
                             limits=Limits(burst_s=0))


async def test_video_note_transcribed_archived_saved(bot, session, storage):
    speech = FakeSpeech(result="сделай стрим про монтаж")
    await _dp(storage, speech).feed_update(bot, make_update(video_note=True, message_id=31))
    [row] = await storage.list_all()
    assert (row.kind, row.text, row.voice_file_id) == ("video_note", "сделай стрим про монтаж", "vn-1")
    assert Path(row.voice_path).name == f"{VIEWER_ID}_31_vnu-1.mp4"
    assert speech.calls[0][1].endswith(".mp4")
    assert len(session.reactions()) == 1
    [note] = session.sent_to(AUTHOR_ID)
    assert "⭕" in note and "[кружок]" in note


async def test_video_caption_and_transcript_combined(bot, session, storage):
    await _dp(storage, FakeSpeech(result="речь")).feed_update(bot, make_update(video=True, caption="подпись"))
    [row] = await storage.list_all()
    assert row.kind == "video" and "подпись" in row.text and "речь" in row.text


async def test_silent_video_note_still_saved(bot, session, storage):
    await _dp(storage, FakeSpeech(result="")).feed_update(bot, make_update(video_note=True))
    [row] = await storage.list_all()
    assert row.text == texts.NO_SPEECH
    assert len(session.reactions()) == 1


async def test_photo_saved_from_caption_without_download(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(photo=True, caption="мем про ИИ"))
    [row] = await storage.list_all()
    assert (row.kind, row.text, row.voice_file_id) == ("photo", "мем про ИИ", "ph-1")
    assert session.calls(GetFile) == []
    assert "[фото]" in session.sent_to(AUTHOR_ID)[0]


async def test_photo_without_caption_saved_with_placeholder(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(photo=True))
    [row] = await storage.list_all()
    assert row.text == texts.NO_CAPTION


async def test_oversized_video_rejected(bot, session, storage):
    dp = create_dispatcher(storage=storage, speech=FakeSpeech(), summarizer=FakeSummarizer(),
                           limits=Limits(max_voice_bytes=10, burst_s=0))
    await dp.feed_update(bot, make_update(video=True, voice_size=11))
    assert session.sent_to(VIEWER_ID) == [texts.MEDIA_TOO_LARGE]
    assert await storage.count() == 0


async def test_documents_not_accepted(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(document=True))
    assert session.sent_to(VIEWER_ID) == [texts.UNSUPPORTED]
    assert await storage.count() == 0


async def test_old_database_accepts_new_kinds(tmp_path):
    import sqlite3

    from bot.storage import Storage

    path = tmp_path / "v0.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,"
                " username TEXT, kind TEXT NOT NULL CHECK (kind IN ('text', 'voice')), text TEXT NOT NULL,"
                " chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, created_at TEXT NOT NULL)")
    con.execute("INSERT INTO suggestions VALUES (1, 5, 'ann', 'voice', 'старое', 5, 1, '2026-09-16T00:00:00')")
    con.commit()
    con.close()
    s = Storage(str(path))
    await s.open()
    new_id = await s.add(user_id=6, username="bob", kind="video_note", text="кружок", chat_id=6, message_id=2)
    rows = await s.list_all()
    assert [(r.id, r.kind, r.text) for r in rows] == [(1, "voice", "старое"), (new_id, "video_note", "кружок")]
    assert new_id == 2
    await s.close()
