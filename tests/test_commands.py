from aiogram.methods import SetMyCommands
from aiogram.types import BotCommandScopeChat, BotCommandScopeDefault

from bot.commands import setup_commands


def _names(call: SetMyCommands) -> list[str]:
    return [c.command for c in call.commands]


async def test_everyone_sees_public_commands_without_summary(bot, session):
    await setup_commands(bot, [])
    [call] = session.calls(SetMyCommands)
    assert isinstance(call.scope, BotCommandScopeDefault)
    assert _names(call) == ["start"]
    assert all(c.description for c in call.commands)


async def test_each_admin_chat_also_sees_admin_commands(bot, session):
    await setup_commands(bot, [777, 888])
    calls = session.calls(SetMyCommands)
    assert len(calls) == 3
    admin_calls = [c for c in calls if isinstance(c.scope, BotCommandScopeChat)]
    assert [c.scope.chat_id for c in admin_calls] == [777, 888]
    assert _names(admin_calls[0]) == ["addadmin", "admins", "original", "removeadmin", "start", "summary", "version", "whoami"]
