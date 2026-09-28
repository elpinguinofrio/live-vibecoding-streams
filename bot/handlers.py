import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message, MessageReactionUpdated, ReactionTypeEmoji, ReplyParameters

from bot import texts
from bot.commands import hide_admin_menu, show_admin_menu
from bot.intake import OK_HAND, Intake, Job, Speech
from bot.notify import Notifier, keyboard
from bot.storage import Notification, Storage
from bot.summary import Assist, split_message
from bot.version import get_version
from bot.voice_archive import VoiceArchive

log = logging.getLogger(__name__)

# Header of notifications sent before replies were tracked: "🆕 Новое предложение 💬 от @x (id 123):".
# Anchored at the end of the first line, so an "(id N)" inside a sender's full name can't redirect replies.
_LEGACY_HEADER = re.compile(r"^🆕 Новое предложение .* \(id (\d+)\):$")
_LEGACY_MATCH_CHARS = 100


@dataclass(frozen=True)
class Limits:
    max_voice_bytes: int = 19 * 1024 * 1024  # Bot API download limit is 20 MB; Groq free tier 25 MB
    stt_concurrency: int = 4
    stt_timeout_s: float = 30.0
    summary_timeout_s: float = 90.0
    burst_s: float = 15.0  # messages from one person this close together become one admin notification
    tldr_timeout_s: float = 30.0
    stt_retry_s: float = 10.0  # failed transcription/save: retry silently this often
    stt_max_attempts: int = 30  # ~5 min, then saved anyway as «не удалось распознать»


class Summarizer(Protocol):
    async def summarize(self, texts: list[str]) -> str: ...

    async def assist(self, text: str) -> Assist: ...


def _display_name(message: Message) -> str | None:
    user = message.from_user
    if user is None:
        return None
    return user.username or user.full_name


async def _answer_safely(message: Message, text: str) -> bool:
    try:
        await message.answer(text)
        return True
    except Exception as exc:
        log.error("failed to send reply: %s", type(exc).__name__)
        return False


def _sender_label(message: Message) -> str:
    user = message.from_user
    handle = f"@{user.username}" if user.username else user.full_name
    return f"{handle} (id {user.id})"


def _admin_label(user_id: int, username: str | None) -> str:
    return f"@{username} (id {user_id})" if username else f"id {user_id}"


async def _is_admin(message: Message, storage: Storage, failed_text: str) -> bool:
    try:
        if await storage.is_admin(message.from_user.id):
            return True
    except Exception as exc:  # the sender may well be an admin: report the failure, not a refusal
        log.error("failed to check admin: %s", type(exc).__name__)
        await _answer_safely(message, failed_text)
        return False
    await _answer_safely(message, texts.NOT_ADMIN)
    return False


def _is_command(message: Message, name: str) -> bool:
    words = (message.text or "").split()
    return bool(words) and words[0].split("@")[0] == f"/{name}"


async def _legacy_notification(storage: Storage, reply: Message, bot: Bot) -> Notification | None:
    """Rebuild the target of an old notification from its text: sender id from the header, originals by text."""
    if reply.from_user is None or reply.from_user.id != bot.id or not reply.text:
        return None
    match = _LEGACY_HEADER.match(reply.text.split("\n", 1)[0])
    if match is None:
        return None
    user_id = int(match.group(1))
    sources = [s.message_id for s in await storage.list_by_user(user_id)
               if s.chat_id == user_id and s.text[:_LEGACY_MATCH_CHARS] in reply.text]
    return Notification(user_chat_id=user_id, source_message_ids=sources)


async def _notification_reply(message: Message, storage: Storage, bot: Bot) -> dict | bool:
    """Filter: an admin replying to one of the bot's suggestion notifications."""
    reply = message.reply_to_message
    if reply is None:
        return False
    try:
        notification = (await storage.get_notification(message.chat.id, reply.message_id)
                        or await _legacy_notification(storage, reply, bot))
        if notification is None or not await storage.is_admin(message.from_user.id):
            return False
    except Exception as exc:
        log.error("failed to look up notification: %s", type(exc).__name__)
        return False
    return {"notification": notification}


async def _admin_plain_message(message: Message, storage: Storage) -> dict | bool:
    """Filter: an admin's ordinary message (not a command) — an answer to the person of their latest notification."""
    if (message.text or "").startswith("/"):
        return False
    try:
        if not await storage.is_admin(message.from_user.id):
            return False
        return {"notification": await storage.latest_notification(message.chat.id)}
    except Exception as exc:
        log.error("failed to look up latest notification: %s", type(exc).__name__)
        return False


PREVIEW_CHARS = 60
_MEDIA_LABELS = {"voice": "голосовое", "video_note": "кружок", "video": "видео", "photo": "фото", "sticker": "стикер"}


def _preview(message: Message) -> str:
    text = (message.text or message.caption or "").strip()
    if not text:
        text = next((label for attr, label in _MEDIA_LABELS.items() if getattr(message, attr, None)), "сообщение")
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS - 1] + "…"


async def _remember_outgoing(storage: Storage, notification: Notification, message_id: int, preview: str,
                             admin_chat_id: int, admin_message_id: int) -> None:
    try:
        await storage.add_outgoing(viewer_chat_id=notification.user_chat_id, message_id=message_id,
                                   source_message_ids=notification.source_message_ids, preview=preview,
                                   admin_chat_id=admin_chat_id, admin_message_id=admin_message_id)
    except Exception as exc:  # only reaction context is lost
        log.error("failed to remember outgoing message: %s", type(exc).__name__)


async def _relay(message: Message, bot: Bot, notification: Notification, storage: Storage) -> None:
    try:  # copy, not forward: the person sees the bot, never the admin
        sent = await bot.copy_message(
            chat_id=notification.user_chat_id, from_chat_id=message.chat.id, message_id=message.message_id,
            reply_parameters=ReplyParameters(message_id=notification.source_message_ids[-1],
                                             allow_sending_without_reply=True)
            if notification.source_message_ids else None,
        )
    except Exception as exc:
        log.error("failed to relay admin reply: %s", type(exc).__name__)
        await _answer_safely(message, texts.RELAY_FAILED)
        return
    await _remember_outgoing(storage, notification, sent.message_id, _preview(message),
                             admin_chat_id=message.chat.id, admin_message_id=message.message_id)
    try:
        await message.react(OK_HAND)
    except Exception:
        await _answer_safely(message, texts.RELAY_SENT)


def _job(message: Message, kind: str, **fields) -> Job:
    return Job(chat_id=message.chat.id, message_id=message.message_id, user_id=message.from_user.id,
               username=_display_name(message), sender=_sender_label(message), kind=kind, **fields)


async def _resolve_target(storage: Storage, arg: str | None) -> tuple[int | None, bool]:
    """Returns (user_id, argument_given)."""
    arg = (arg or "").strip()
    if not arg:
        return None, False
    if arg.lstrip("-").isdigit():
        return int(arg), True
    return await storage.find_user(arg), True


def create_router(limits: Limits) -> Router:
    router = Router(name="suggestions")
    router.message.filter(F.chat.type == ChatType.PRIVATE)

    @router.message(_notification_reply)
    async def on_notification_reply(message: Message, bot: Bot, storage: Storage, notification: Notification) -> None:
        if _is_command(message, "original"):
            if not notification.source_message_ids:
                await _answer_safely(message, texts.ORIGINAL_FAILED)
                return
            for source_id in notification.source_message_ids:
                try:
                    await bot.copy_message(chat_id=message.chat.id, from_chat_id=notification.user_chat_id,
                                           message_id=source_id)
                except Exception as exc:
                    log.error("failed to copy original: %s", type(exc).__name__)
                    await _answer_safely(message, texts.ORIGINAL_FAILED)
                    return
            return
        await _relay(message, bot, notification, storage)

    @router.message_reaction(F.chat.type == ChatType.PRIVATE)
    async def on_reaction(event: MessageReactionUpdated, bot: Bot, storage: Storage) -> None:
        """A viewer reacted in their chat with the bot. On an admin's answer: mirror the emoji onto that admin's own
        message (👌 again when removed). Anything else, or if Telegram refuses the emoji: a short note instead."""
        emojis = lambda rs: [getattr(r, "emoji", None) or "⭐" for r in rs]  # noqa: E731 (custom/paid → ⭐)
        new, old = emojis(event.new_reaction), emojis(event.old_reaction)
        try:
            if event.user is None or await storage.is_admin(event.user.id):
                return
            outgoing = await storage.get_outgoing(event.chat.id, event.message_id)
        except Exception as exc:
            log.error("failed to handle reaction: %s", type(exc).__name__)
            return
        if outgoing and outgoing["admin_message_id"]:
            try:
                mirror = [ReactionTypeEmoji(emoji=new[-1])] if new else OK_HAND  # bots show one reaction
                await bot.set_message_reaction(chat_id=outgoing["admin_chat_id"],
                                               message_id=outgoing["admin_message_id"], reaction=mirror)
                return
            except Exception as exc:  # e.g. an emoji bots may not set
                log.warning("failed to mirror reaction: %s", type(exc).__name__)
        user = event.user
        sender = f"@{user.username} (id {user.id})" if user.username else f"{user.full_name} (id {user.id})"
        target = (texts.REACTION_TARGET_ANSWER.format(preview=outgoing["preview"]) if outgoing
                  else texts.REACTION_TARGET_OTHER)
        note = (texts.REACTION_NEW.format(emoji=" ".join(new), sender=sender, target=target) if new
                else texts.REACTION_REMOVED.format(emoji=" ".join(old), sender=sender, target=target))
        try:  # a mirrorable answer that failed: tell its admin, under their answer; otherwise tell everyone
            recipients = ({outgoing["admin_chat_id"]: outgoing["admin_message_id"]}
                          if outgoing and outgoing["admin_message_id"]
                          else {admin_id: None for admin_id in await storage.admin_ids()})
        except Exception as exc:
            log.error("failed to read admins: %s", type(exc).__name__)
            return
        for admin_id, under in recipients.items():
            try:
                reply = ReplyParameters(message_id=under, allow_sending_without_reply=True) if under else None
                await bot.send_message(admin_id, note, reply_parameters=reply)
            except Exception as exc:
                log.error("failed to report reaction: %s", type(exc).__name__)

    @router.callback_query(F.data.in_({"react", "draft"}))
    async def on_quick_action(callback: CallbackQuery, bot: Bot, storage: Storage) -> None:
        try:
            if not await storage.is_admin(callback.from_user.id):
                await callback.answer(texts.NOT_ADMIN)
                return
            note = callback.message
            notification = await storage.get_notification(note.chat.id, note.message_id)
            if notification is None or not notification.source_message_ids:
                await callback.answer(texts.BUTTON_FAILED)
                return
            target = notification.source_message_ids[-1]
            if callback.data == "react":
                await bot.set_message_reaction(chat_id=notification.user_chat_id, message_id=target,
                                               reaction=[ReactionTypeEmoji(emoji=notification.emoji or "👍")])
                remaining = keyboard(None, notification.draft)
            else:
                sent = await bot.send_message(notification.user_chat_id, notification.draft,
                                              reply_parameters=ReplyParameters(message_id=target,
                                                                               allow_sending_without_reply=True))
                await _remember_outgoing(storage, notification, sent.message_id, notification.draft[:PREVIEW_CHARS],
                                         admin_chat_id=note.chat.id, admin_message_id=note.message_id)
                remaining = keyboard(notification.emoji, None)
        except Exception as exc:
            log.error("quick action %s failed: %s", callback.data, type(exc).__name__)
            try:
                await callback.answer(texts.BUTTON_FAILED)
            except Exception:
                pass
            return
        try:
            await callback.answer(texts.BUTTON_DONE)
            await bot.edit_message_reply_markup(chat_id=note.chat.id, message_id=note.message_id,
                                                reply_markup=remaining)
        except Exception as exc:
            log.error("failed to update buttons: %s", type(exc).__name__)

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        await _answer_safely(message, texts.WELCOME)

    @router.message(Command("whoami"))
    async def on_whoami(message: Message) -> None:
        await _answer_safely(message, texts.WHOAMI.format(user_id=message.from_user.id))

    @router.message(Command("version"))
    async def on_version(message: Message) -> None:
        version = await asyncio.to_thread(get_version)
        await _answer_safely(message, texts.VERSION.format(version=version))

    @router.message(Command("summary"))
    async def on_summary(message: Message, storage: Storage, summarizer: Summarizer) -> None:
        if not await _is_admin(message, storage, texts.SUMMARY_FAILED):
            return
        try:
            async with asyncio.timeout(limits.summary_timeout_s):
                items = await storage.list_all()
                summary = await summarizer.summarize([i.text for i in items]) if items else None
        except TimeoutError:
            log.error("summary timed out")
            await _answer_safely(message, texts.SUMMARY_TIMEOUT)
            return
        except Exception as exc:
            log.error("summary failed: %s", type(exc).__name__)
            await _answer_safely(message, texts.SUMMARY_FAILED)
            return
        if not items:
            await _answer_safely(message, texts.NO_SUGGESTIONS)
            return
        for chunk in split_message(summary or texts.SUMMARY_FAILED):
            if not await _answer_safely(message, chunk):
                break

    @router.message(Command("original"))
    async def on_original(message: Message, storage: Storage) -> None:
        if await _is_admin(message, storage, texts.ORIGINAL_FAILED):
            await _answer_safely(message, texts.ORIGINAL_USAGE)

    @router.message(Command("admins"))
    async def on_admins(message: Message, storage: Storage) -> None:
        if not await _is_admin(message, storage, texts.ADMIN_FAILED):
            return
        try:
            admins = await storage.list_admins()
        except Exception as exc:
            log.error("failed to list admins: %s", type(exc).__name__)
            await _answer_safely(message, texts.ADMIN_FAILED)
            return
        lines = "\n".join(f"• {_admin_label(a.user_id, a.username)}" for a in admins)
        await _answer_safely(message, texts.ADMINS_LIST.format(lines=lines))

    @router.message(Command("addadmin"))
    async def on_add_admin(message: Message, command: CommandObject, bot: Bot, storage: Storage) -> None:
        if not await _is_admin(message, storage, texts.ADMIN_FAILED):
            return
        try:
            user_id, given = await _resolve_target(storage, command.args)
            if not given:
                await _answer_safely(message, texts.ADD_ADMIN_USAGE)
                return
            if user_id is None:
                await _answer_safely(message, texts.ADMIN_UNKNOWN_USER)
                return
            username = await storage.known_username(user_id)
            added = await storage.add_admin(user_id, username, added_by=message.from_user.id)
        except Exception as exc:
            log.error("failed to add admin: %s", type(exc).__name__)
            await _answer_safely(message, texts.ADMIN_FAILED)
            return
        who = _admin_label(user_id, username)
        if added:
            await show_admin_menu(bot, user_id)
        await _answer_safely(message, (texts.ADMIN_ADDED if added else texts.ADMIN_ALREADY).format(who=who))

    @router.message(Command("removeadmin"))
    async def on_remove_admin(message: Message, command: CommandObject, bot: Bot, storage: Storage) -> None:
        if not await _is_admin(message, storage, texts.ADMIN_FAILED):
            return
        try:
            user_id, given = await _resolve_target(storage, command.args)
            if not given:
                await _answer_safely(message, texts.REMOVE_ADMIN_USAGE)
                return
            if user_id is None:
                await _answer_safely(message, texts.ADMIN_UNKNOWN_USER)
                return
            admin = await storage.get_admin(user_id)
            result = await storage.remove_admin(user_id)
        except Exception as exc:
            log.error("failed to remove admin: %s", type(exc).__name__)
            await _answer_safely(message, texts.ADMIN_FAILED)
            return
        who = _admin_label(user_id, admin.username if admin else None)
        if result == "removed":
            await hide_admin_menu(bot, user_id)
            await _answer_safely(message, texts.ADMIN_REMOVED.format(who=who))
        elif result == "last":
            await _answer_safely(message, texts.ADMIN_LAST)
        else:
            await _answer_safely(message, texts.ADMIN_MISSING.format(who=who))

    @router.message(_admin_plain_message)
    async def on_admin_plain(message: Message, bot: Bot, storage: Storage, notification: Notification | None) -> None:
        if notification is None:
            await _answer_safely(message, texts.ADMIN_NO_TARGET)
            return
        await _relay(message, bot, notification, storage)

    @router.message(F.text & ~F.text.startswith("/"))
    async def on_text(message: Message, bot: Bot, intake: Intake) -> None:
        await intake.submit(bot, _job(message, "text", text=message.text.strip()))

    @router.message(F.photo)
    async def on_photo(message: Message, bot: Bot, intake: Intake) -> None:
        text = (message.caption or "").strip() or texts.NO_CAPTION
        await intake.submit(bot, _job(message, "photo", text=text, file_id=message.photo[-1].file_id))

    @router.message(F.voice | F.video_note | F.video)
    async def on_media(message: Message, bot: Bot, intake: Intake) -> None:
        kind = "voice" if message.voice else "video_note" if message.video_note else "video"
        media = getattr(message, kind)
        if media.file_size is not None and media.file_size > limits.max_voice_bytes:
            await _answer_safely(message, texts.VOICE_TOO_LARGE if kind == "voice" else texts.MEDIA_TOO_LARGE)
            return
        await intake.submit(bot, _job(message, kind, caption=message.caption or "", file_id=media.file_id,
                                      file_unique_id=media.file_unique_id, duration=media.duration))

    @router.message()
    async def on_other(message: Message) -> None:
        await _answer_safely(message, texts.UNSUPPORTED)

    return router


def create_dispatcher(*, storage: Storage, speech: Speech, summarizer: Summarizer,
                      limits: Limits = Limits(), voice_archive: VoiceArchive | None = None) -> Dispatcher:
    dp = Dispatcher()
    dp["storage"] = storage
    # Default: originals live next to the database (local disk), e.g. ~/.local/share/<app>/voices/
    dp["voice_archive"] = voice_archive or VoiceArchive(Path(storage.path).parent / "voices")
    dp["speech"] = speech
    dp["summarizer"] = summarizer
    dp["notifier"] = notifier = Notifier(storage, summarizer, burst_s=limits.burst_s,
                                         tldr_timeout_s=limits.tldr_timeout_s)
    dp["intake"] = Intake(storage, speech, dp["voice_archive"], notifier, concurrency=limits.stt_concurrency,
                          timeout_s=limits.stt_timeout_s, retry_s=limits.stt_retry_s,
                          max_attempts=limits.stt_max_attempts)
    dp.include_router(create_router(limits))
    return dp
