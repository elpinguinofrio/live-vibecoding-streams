import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

log = logging.getLogger(__name__)

PUBLIC_COMMANDS = [
    BotCommand(command="start", description="Как предложить тему"),
]
# /version and /whoami still work for everyone when typed; only admins see them in the menu
ADMIN_ONLY_COMMANDS = [
    BotCommand(command="version", description="Версия бота"),
    BotCommand(command="whoami", description="Мой Telegram ID"),
    BotCommand(command="summary", description="Сводка всех предложений (админы)"),
    BotCommand(command="original", description="Оригинал: ответьте так на уведомление"),
    BotCommand(command="admins", description="Список админов"),
    BotCommand(command="addadmin", description="Добавить админа: id или @username"),
    BotCommand(command="removeadmin", description="Убрать админа: id или @username"),
]
ADMIN_COMMANDS = sorted([*PUBLIC_COMMANDS, *ADMIN_ONLY_COMMANDS], key=lambda c: c.command)


async def show_admin_menu(bot: Bot, user_id: int) -> None:
    try:  # fails if the user never opened the bot; the commands still work for them
        await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=user_id))
    except Exception as exc:
        log.warning("failed to set admin menu: %s", type(exc).__name__)


async def hide_admin_menu(bot: Bot, user_id: int) -> None:
    try:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=user_id))
    except Exception as exc:
        log.warning("failed to reset admin menu: %s", type(exc).__name__)


async def setup_commands(bot: Bot, admin_ids: list[int]) -> None:
    """Register the "/" menu: public commands for everyone, plus admin commands in each admin's private chat."""
    await bot.set_my_commands(PUBLIC_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in admin_ids:
        await show_admin_menu(bot, admin_id)
