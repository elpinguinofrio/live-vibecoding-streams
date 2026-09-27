import asyncio
import logging
from contextlib import AsyncExitStack

from aiogram import Bot
from groq import AsyncGroq

from bot.commands import setup_commands
from bot.config import load_settings
from bot.handlers import Limits, create_dispatcher
from bot.redact import RedactingFormatter
from bot.speech import GroqSpeech
from bot.storage import Storage
from bot.summary import GroqSummarizer

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


async def main() -> None:
    settings = load_settings()
    token = settings.telegram_bot_token.get_secret_value()
    api_key = settings.groq_api_key.get_secret_value()

    logging.basicConfig(level=logging.INFO)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(RedactingFormatter(LOG_FORMAT, secrets=[token, api_key]))
    async with AsyncExitStack() as stack:
        storage = Storage(settings.db_path)
        await storage.open()
        stack.push_async_callback(storage.close)
        if await storage.seed_admins(settings.seed_admin_ids()):
            logging.info("admins seeded from ADMIN_USER_IDS / AUTHOR_USER_ID")
        admin_ids = await storage.admin_ids()
        if not admin_ids:
            logging.warning("no admins: set ADMIN_USER_IDS (DM /whoami to the bot to get an id); /summary is disabled")

        # The SDK retries connection errors, 408/409/429 and 5xx with exponential backoff and jitter.
        groq = AsyncGroq(api_key=api_key, timeout=settings.groq_timeout_s, max_retries=settings.groq_max_retries)
        stack.push_async_callback(groq.close)

        bot = Bot(token=token)
        stack.push_async_callback(bot.session.close)

        dp = create_dispatcher(
            storage=storage,
            speech=GroqSpeech(groq, settings.groq_stt_model),
            summarizer=GroqSummarizer(groq, settings.groq_summary_model),
            limits=Limits(
                max_voice_bytes=settings.max_voice_bytes,
                stt_concurrency=settings.stt_concurrency,
                stt_timeout_s=settings.stt_timeout_s,
                summary_timeout_s=settings.summary_timeout_s,
            ),
        )
        stack.push_async_callback(dp["notifier"].close)  # runs before the bot session closes
        stack.push_async_callback(dp["intake"].close)  # stop retries first; unfinished media stays queued in the DB
        await dp["intake"].resume(bot)
        await setup_commands(bot, admin_ids)
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
