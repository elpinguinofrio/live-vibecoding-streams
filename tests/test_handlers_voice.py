from aiogram.types import ReactionTypeEmoji

from bot import texts
from bot.handlers import create_dispatcher
from tests.conftest import AUTHOR_ID, VIEWER_ID, FakeSpeech, FakeSummarizer, make_update


def _dp(storage, speech):
    return create_dispatcher(storage=storage, speech=speech, summarizer=FakeSummarizer())


async def test_voice_transcribed_saved_thumbs_up(bot, session, storage):
    speech = FakeSpeech(result="сделай выпуск про роботов")
    await _dp(storage, speech).feed_update(bot, make_update(voice=True, message_id=77))
    assert speech.calls and speech.calls[0][0] == session.file_bytes
    items = await storage.list_all()
    assert [(i.kind, i.text, i.message_id) for i in items] == [("voice", "сделай выпуск про роботов", 77)]
    [reaction] = session.reactions()
    assert reaction.message_id == 77 and reaction.reaction == [ReactionTypeEmoji(emoji="👌")]
    assert session.sent_to(VIEWER_ID) == []


def _retrying(storage, speech, **limits):
    return create_dispatcher(storage=storage, speech=speech, summarizer=FakeSummarizer(),
                             limits=Limits(**{"burst_s": 0, "stt_retry_s": 0.01, **limits}))


async def test_voice_stt_error_retried_silently_until_saved(bot, session, storage):
    speech = FakeSpeech(result="про монтаж", fail_times=2)
    dp = _retrying(storage, speech)
    await dp.feed_update(bot, make_update(voice=True, message_id=5))
    assert await storage.count() == 0 and session.reactions() == []  # not yet: still retrying
    await dp["intake"].drain()
    assert len(speech.calls) == 3
    [row] = await storage.list_all()
    assert row.text == "про монтаж"
    assert [r.message_id for r in session.reactions()] == [5]
    assert session.sent_to(VIEWER_ID) == []  # the user never hears about the failures
    assert await storage.pending_jobs() == []


async def test_voice_empty_transcript_saved_without_telling_user(bot, session, storage):
    await _dp(storage, FakeSpeech(result="")).feed_update(bot, make_update(voice=True))
    [row] = await storage.list_all()
    assert row.text == texts.NO_SPEECH
    assert len(session.reactions()) == 1
    assert session.sent_to(VIEWER_ID) == []


async def test_voice_gives_up_after_max_attempts_but_keeps_message(bot, session, storage):
    speech = FakeSpeech(error=RuntimeError("down for good"))
    dp = _retrying(storage, speech, stt_max_attempts=3)
    await dp.feed_update(bot, make_update(voice=True, message_id=6))
    await dp["intake"].drain()
    assert len(speech.calls) == 3
    [row] = await storage.list_all()
    assert row.text == texts.STT_GAVE_UP and row.voice_path  # admins can still /original it
    assert len(session.reactions()) == 1
    assert session.sent_to(VIEWER_ID) == []
    assert any(texts.STT_GAVE_UP in t for t in session.sent_to(AUTHOR_ID))


async def test_concurrent_voices_with_failures_none_dropped(bot, session, storage):
    speech = FakeSpeech(fail_times=8, delay=0.01)
    dp = _retrying(storage, speech, stt_concurrency=2)
    await asyncio.gather(*(dp.feed_update(bot, make_update(voice=True, message_id=i)) for i in range(8)))
    await dp["intake"].drain()
    assert speech.max_active <= 2
    assert await storage.count() == 8
    assert len(session.reactions()) == 8
    assert session.sent_to(VIEWER_ID) == []


async def test_pending_voice_survives_restart(bot, session, storage):
    dp = _retrying(storage, FakeSpeech(error=RuntimeError("down")), stt_retry_s=60)
    await dp.feed_update(bot, make_update(voice=True, message_id=7))
    await dp["intake"].close()  # bot stops while the job waits for its retry
    assert await storage.count() == 0 and len(await storage.pending_jobs()) == 1
    dp2 = _retrying(storage, FakeSpeech(result="после рестарта"))
    await dp2["intake"].resume(bot)
    await dp2["intake"].drain()
    [row] = await storage.list_all()
    assert (row.text, row.message_id) == ("после рестарта", 7)
    assert await storage.pending_jobs() == []


import asyncio

from bot.handlers import Limits


async def test_oversized_voice_rejected_before_download(bot, session, storage):
    speech = FakeSpeech()
    dp = create_dispatcher(storage=storage, speech=speech, summarizer=FakeSummarizer(),
                           limits=Limits(max_voice_bytes=1000))
    await dp.feed_update(bot, make_update(voice=True, voice_size=1001))
    assert speech.calls == []
    assert [type(r).__name__ for r in session.requests] == ["SendMessage"]  # no GetFile
    assert session.sent_texts() == [texts.VOICE_TOO_LARGE]
    assert await storage.count() == 0


async def test_voice_concurrency_is_bounded(bot, session, storage):
    speech = FakeSpeech(delay=0.05)
    dp = create_dispatcher(storage=storage, speech=speech, summarizer=FakeSummarizer(),
                           limits=Limits(stt_concurrency=2))
    await asyncio.gather(*(dp.feed_update(bot, make_update(voice=True, message_id=i)) for i in range(8)))
    assert speech.max_active == 2
    assert await storage.count() == 8
    assert len(session.reactions()) == 8


async def test_voice_timeout_retried_silently(bot, session, storage):
    dp = _retrying(storage, FakeSpeech(delay=1), stt_timeout_s=0.05, stt_max_attempts=2)
    await dp.feed_update(bot, make_update(voice=True))
    await dp["intake"].drain()
    assert session.sent_to(VIEWER_ID) == []
    [row] = await storage.list_all()
    assert row.text == texts.STT_GAVE_UP


async def test_voice_download_failure(bot, session, storage):
    async def broken_stream(*args, **kwargs):
        raise RuntimeError("download failed")
        yield b""

    session.stream_content = broken_stream
    speech = FakeSpeech()
    dp = _retrying(storage, speech, stt_max_attempts=2)
    await dp.feed_update(bot, make_update(voice=True))
    await dp["intake"].drain()
    assert speech.calls == []
    assert session.sent_to(VIEWER_ID) == []
    [row] = await storage.list_all()
    assert row.text == texts.STT_GAVE_UP and row.voice_path is None


from pathlib import Path

from bot.voice_archive import VoiceArchive


async def test_voice_original_archived_next_to_db_and_linked_in_row(bot, session, storage):
    await _dp(storage, FakeSpeech(result="про монтаж")).feed_update(bot, make_update(voice=True, message_id=55))
    [row] = await storage.list_all()
    expected = Path(storage.path).parent / "voices" / f"{VIEWER_ID}_55_vu-1.ogg"
    assert Path(row.voice_path) == expected
    assert expected.read_bytes() == session.file_bytes
    assert row.voice_file_id == "voice-1"
    assert row.stt_model == FakeSpeech.model
    assert len(session.reactions()) == 1


async def test_reaction_only_after_row_and_original_exist(bot, session, storage):
    seen = []

    async def check(method):
        [row] = await storage.list_all()
        seen.append(Path(row.voice_path).is_file())

    session.on_reaction = check
    await _dp(storage, FakeSpeech()).feed_update(bot, make_update(voice=True))
    assert seen == [True]


async def test_archive_failure_retried_silently(bot, session, storage):
    class FlakyArchive(VoiceArchive):
        failures = 1

        async def save(self, *args, **kwargs):
            if self.failures:
                self.failures -= 1
                raise OSError("disk full")
            return await super().save(*args, **kwargs)

    speech = FakeSpeech()
    dp = create_dispatcher(storage=storage, speech=speech, summarizer=FakeSummarizer(),
                           voice_archive=FlakyArchive(Path(storage.path).parent / "voices"),
                           limits=Limits(burst_s=0, stt_retry_s=0.01))
    await dp.feed_update(bot, make_update(voice=True))
    assert session.reactions() == []
    await dp["intake"].drain()
    assert await storage.count() == 1 and len(session.reactions()) == 1
    assert session.sent_to(VIEWER_ID) == []


async def test_stt_failure_still_keeps_original_for_later(bot, session, storage):
    dp = _retrying(storage, FakeSpeech(error=RuntimeError("stt down")), stt_retry_s=60)
    await dp.feed_update(bot, make_update(voice=True, message_id=9))
    assert await storage.count() == 0
    assert (Path(storage.path).parent / "voices" / f"{VIEWER_ID}_9_vu-1.ogg").is_file()
    assert session.sent_to(VIEWER_ID) == []
    await dp["intake"].close()
