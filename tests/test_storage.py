import pytest

from bot.storage import Storage


async def test_add_and_list(storage: Storage):
    await storage.add(user_id=5, username="ann", kind="text", text="идея", chat_id=5, message_id=1)
    await storage.add(user_id=6, username=None, kind="voice", text="голос", chat_id=6, message_id=2)
    items = await storage.list_all()
    assert [(i.kind, i.text, i.username) for i in items] == [("text", "идея", "ann"), ("voice", "голос", None)]
    assert await storage.count() == 2


async def test_rejects_bad_kind(storage: Storage):
    with pytest.raises(ValueError):
        await storage.add(user_id=5, username="a", kind="sticker", text="x", chat_id=5, message_id=1)


async def test_rejects_empty_text(storage: Storage):
    with pytest.raises(ValueError):
        await storage.add(user_id=5, username="a", kind="text", text="   ", chat_id=5, message_id=1)
    assert await storage.count() == 0


async def test_persists_across_reopen(tmp_path):
    path = str(tmp_path / "db.sqlite")
    s = Storage(path)
    await s.open()
    await s.add(user_id=1, username="a", kind="text", text="keep", chat_id=1, message_id=1)
    await s.close()
    s2 = Storage(path)
    await s2.open()
    assert [i.text for i in await s2.list_all()] == ["keep"]
    await s2.close()


async def test_duplicate_message_is_idempotent(storage: Storage):
    first = await storage.add(user_id=5, username="a", kind="text", text="идея", chat_id=5, message_id=9)
    second = await storage.add(user_id=5, username="a", kind="text", text="идея", chat_id=5, message_id=9)
    assert first is not None and second is None
    assert await storage.count() == 1


async def test_wal_and_busy_timeout(storage: Storage):
    db = storage._conn()
    assert (await (await db.execute("PRAGMA journal_mode")).fetchone())[0] == "wal"
    assert (await (await db.execute("PRAGMA busy_timeout")).fetchone())[0] >= 5000


async def test_expands_home_in_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    s = Storage("~/sub/db.sqlite")
    await s.open()
    await s.close()
    assert (tmp_path / "sub" / "db.sqlite").is_file()


async def test_concurrent_adds_all_persist(storage: Storage):
    import asyncio

    await asyncio.gather(*(
        storage.add(user_id=i, username=None, kind="text", text=f"t{i}", chat_id=i, message_id=i) for i in range(50)
    ))
    assert await storage.count() == 50


async def test_voice_fields_persisted(storage: Storage):
    await storage.add(user_id=1, username="a", kind="voice", text="голос", chat_id=1, message_id=1,
                      voice_path="/v/1_1_u.ogg", voice_file_id="FILE", stt_model="whisper-large-v3")
    await storage.add(user_id=1, username="a", kind="text", text="текст", chat_id=1, message_id=2)
    voice, text = await storage.list_all()
    assert (voice.voice_path, voice.voice_file_id, voice.stt_model) == ("/v/1_1_u.ogg", "FILE", "whisper-large-v3")
    assert (text.voice_path, text.voice_file_id, text.stt_model) == (None, None, None)


async def test_legacy_db_gains_voice_columns_and_keeps_rows(tmp_path):
    import aiosqlite

    path = str(tmp_path / "legacy.db")
    async with aiosqlite.connect(path) as db:
        await db.execute("""CREATE TABLE suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            username TEXT, kind TEXT NOT NULL CHECK (kind IN ('text','voice')), text TEXT NOT NULL,
            chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, created_at TEXT NOT NULL)""")
        await db.execute("INSERT INTO suggestions (user_id, username, kind, text, chat_id, message_id, created_at)"
                         " VALUES (1, 'a', 'voice', 'old', 1, 1, 't')")
        await db.commit()
    s = Storage(path)
    await s.open()
    [old] = await s.list_all()
    assert (old.text, old.voice_path, old.stt_model) == ("old", None, None)
    await s.add(user_id=1, username="a", kind="voice", text="new", chat_id=1, message_id=2,
                voice_path="p", voice_file_id="f", stt_model="m")
    assert (await s.list_all())[1].stt_model == "m"
    await s.close()
    s2 = Storage(path)
    await s2.open()  # reopening an already-migrated DB must not fail
    assert await s2.count() == 2
    await s2.close()
