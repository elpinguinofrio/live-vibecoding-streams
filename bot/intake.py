import asyncio
import logging
from dataclasses import asdict, dataclass
from io import BytesIO
from typing import Protocol

from aiogram import Bot
from aiogram.types import ReactionTypeEmoji

from bot import texts
from bot.notify import Notifier
from bot.storage import Storage
from bot.voice_archive import VoiceArchive

log = logging.getLogger(__name__)

OK_HAND = [ReactionTypeEmoji(emoji="👌")]
MEDIA_EXT = {"voice": "ogg", "video_note": "mp4", "video": "mp4"}  # Groq STT accepts both


class Speech(Protocol):
    model: str

    async def transcribe(self, data: bytes, filename: str) -> str: ...


@dataclass
class Job:
    """One incoming viewer message on its way to being saved. For media, `caption` is filled in, not the text."""
    chat_id: int
    message_id: int
    user_id: int
    username: str | None
    sender: str
    kind: str
    caption: str = ""
    file_id: str = ""
    file_unique_id: str = ""
    duration: int | None = None
    attempts: int = 0
    text: str = ""  # text and photo: the final text to save

    @property
    def needs_stt(self) -> bool:
        return self.kind in MEDIA_EXT


def _join(*parts: str) -> str:
    return "\n\n".join(p for p in (s.strip() for s in parts) if p)


class Intake:
    """Saves viewer messages. Failures are retried silently every `retry_s`; viewers only ever see 👌.

    Media jobs are also stored in the DB, so a restart resumes them. After `max_attempts` a message is saved
    anyway (media as «не удалось распознать»), so admins still see it and can fetch the original.
    """

    def __init__(self, storage: Storage, speech: Speech, archive: VoiceArchive, notifier: Notifier, *,
                 concurrency: int, timeout_s: float, retry_s: float, max_attempts: int) -> None:
        self._storage = storage
        self._speech = speech
        self._archive = archive
        self._notifier = notifier
        self._slots = asyncio.Semaphore(concurrency)  # waiting here is the queue: nobody is dropped
        self._timeout_s = timeout_s
        self._retry_s = retry_s
        self._max_attempts = max_attempts
        self._tasks: set[asyncio.Task] = set()

    async def submit(self, bot: Bot, job: Job) -> None:
        if job.needs_stt:
            try:
                await self._storage.enqueue_job(asdict(job))
            except Exception as exc:  # still processed; only restart-survival is lost
                log.error("failed to persist %s job: %s", job.kind, type(exc).__name__)
        if not await self._attempt(bot, job):
            self._spawn(bot, job, delay=True)

    async def resume(self, bot: Bot) -> None:
        """Pick up media jobs left unfinished by a previous run."""
        for row in await self._storage.pending_jobs():
            self._spawn(bot, Job(**row), delay=False)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _spawn(self, bot: Bot, job: Job, *, delay: bool) -> None:
        task = asyncio.create_task(self._retry(bot, job, delay=delay))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _retry(self, bot: Bot, job: Job, *, delay: bool) -> None:
        while True:
            if job.attempts >= self._max_attempts:
                await self._give_up(bot, job)
                return
            if delay:
                await asyncio.sleep(self._retry_s)
            delay = True
            if await self._attempt(bot, job):
                return

    async def _attempt(self, bot: Bot, job: Job) -> bool:
        try:
            voice_path = stt_model = None
            text = job.text
            if job.needs_stt:
                ext = MEDIA_EXT[job.kind]
                async with self._slots, asyncio.timeout(self._timeout_s):
                    data = (await bot.download(job.file_id, destination=BytesIO())).getvalue()
                    # keep the original before transcribing, so it survives STT failures too
                    voice_path = await self._archive.save(data, chat_id=job.chat_id, message_id=job.message_id,
                                                          file_unique_id=job.file_unique_id, ext=ext)
                    transcript = await self._speech.transcribe(data, f"{job.kind}.{ext}")
                text = _join(job.caption, transcript) or texts.NO_SPEECH
                stt_model = self._speech.model
            await self._finish(bot, job, text, voice_path=voice_path, stt_model=stt_model)
            return True
        except Exception as exc:  # incl. timeouts; log type only: download errors carry the token-bearing URL
            job.attempts += 1
            log.warning("%s %s attempt %d failed: %s", job.kind, job.message_id, job.attempts, type(exc).__name__)
            if job.needs_stt:
                try:
                    await self._storage.set_job_attempts(job.chat_id, job.message_id, job.attempts)
                except Exception:
                    pass
            return False

    async def _give_up(self, bot: Bot, job: Job) -> None:
        text = _join(job.caption, texts.STT_GAVE_UP) if job.needs_stt else job.text
        voice_path = None
        if job.needs_stt:  # the original may have been archived by an earlier attempt
            voice_path = await self._archive.existing(chat_id=job.chat_id, message_id=job.message_id,
                                                      file_unique_id=job.file_unique_id, ext=MEDIA_EXT[job.kind])
        try:
            await self._finish(bot, job, text, voice_path=voice_path, stt_model=None)
            log.error("%s %s saved without transcript after %d attempts", job.kind, job.message_id, job.attempts)
        except Exception as exc:
            log.error("dropping %s %s after %d attempts: %s", job.kind, job.message_id, job.attempts,
                      type(exc).__name__)

    async def _finish(self, bot: Bot, job: Job, text: str, *, voice_path: str | None, stt_model: str | None) -> None:
        inserted_id = await self._storage.add(
            user_id=job.user_id, username=job.username, kind=job.kind, text=text, chat_id=job.chat_id,
            message_id=job.message_id, voice_path=voice_path, voice_file_id=job.file_id or None, stt_model=stt_model,
        )  # None: an already-stored duplicate delivery, still acknowledged
        if job.needs_stt:
            try:
                await self._storage.delete_job(job.chat_id, job.message_id)
            except Exception as exc:
                log.error("failed to delete finished job: %s", type(exc).__name__)
        try:
            await bot.set_message_reaction(chat_id=job.chat_id, message_id=job.message_id, reaction=OK_HAND)
        except Exception as exc:
            log.error("failed to set reaction: %s", type(exc).__name__)
            try:
                await bot.send_message(job.chat_id, texts.SAVED_FALLBACK)
            except Exception:
                pass
        if inserted_id is not None:
            await self._notifier.add(bot, chat_id=job.chat_id, sender=job.sender, kind=job.kind, text=text,
                                     message_id=job.message_id, duration=job.duration)
