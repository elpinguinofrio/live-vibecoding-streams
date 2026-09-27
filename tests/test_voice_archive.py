from pathlib import Path

from bot.voice_archive import VoiceArchive


async def test_save_writes_ogg_named_by_chat_message_and_unique_id(tmp_path):
    archive = VoiceArchive(tmp_path / "voices")
    path = await archive.save(b"OggS-data", chat_id=100, message_id=7, file_unique_id="AQADx")
    assert Path(path) == tmp_path / "voices" / "100_7_AQADx.ogg"
    assert Path(path).read_bytes() == b"OggS-data"
    assert list((tmp_path / "voices").iterdir()) == [Path(path)]  # no temp files left behind


async def test_save_is_idempotent_for_duplicate_delivery(tmp_path):
    archive = VoiceArchive(tmp_path)
    first = await archive.save(b"one", chat_id=1, message_id=1, file_unique_id="u")
    second = await archive.save(b"one", chat_id=1, message_id=1, file_unique_id="u")
    assert first == second and Path(first).read_bytes() == b"one"


async def test_expands_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = await VoiceArchive("~/v").save(b"x", chat_id=1, message_id=2, file_unique_id="u")
    assert Path(path) == tmp_path / "v" / "1_2_u.ogg"


async def test_unsafe_unique_id_characters_are_sanitized(tmp_path):
    path = await VoiceArchive(tmp_path).save(b"x", chat_id=1, message_id=2, file_unique_id="../../evil")
    assert Path(path).parent == tmp_path


async def test_save_uses_given_extension(tmp_path):
    path = await VoiceArchive(tmp_path).save(b"mp4", chat_id=1, message_id=2, file_unique_id="u", ext="mp4")
    assert Path(path).name == "1_2_u.mp4"
