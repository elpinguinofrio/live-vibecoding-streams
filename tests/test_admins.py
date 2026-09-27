from aiogram.methods import DeleteMyCommands, SetMyCommands

from bot import texts
from bot.handlers import Limits, create_dispatcher
from bot.storage import Storage
from tests.conftest import AUTHOR_ID, VIEWER_ID, FakeSpeech, FakeSummarizer, make_update

OTHER_ID = 200


def _dp(storage):
    return create_dispatcher(storage=storage, speech=FakeSpeech(), summarizer=FakeSummarizer(), limits=Limits(burst_s=0))


# --- storage ---

async def test_seed_only_fills_empty_table(tmp_path):
    s = Storage(str(tmp_path / "db.sqlite"))
    await s.open()
    assert await s.seed_admins([1, 2]) is True
    await s.remove_admin(2)
    assert await s.seed_admins([1, 2]) is False  # removals survive restarts
    assert await s.admin_ids() == [1]
    await s.close()


async def test_seed_takes_known_usernames(tmp_path):
    s = Storage(str(tmp_path / "db.sqlite"))
    await s.open()
    await s.add(user_id=7, username="wolf", kind="text", text="x", chat_id=7, message_id=1)
    await s.seed_admins([7])
    [admin] = await s.list_admins()
    assert (admin.user_id, admin.username) == (7, "wolf")
    await s.close()


async def test_add_remove_admin(storage: Storage):
    assert await storage.add_admin(OTHER_ID, "bob", added_by=AUTHOR_ID) is True
    assert await storage.add_admin(OTHER_ID, "bob", added_by=AUTHOR_ID) is False
    assert await storage.is_admin(OTHER_ID)
    assert await storage.remove_admin(OTHER_ID) == "removed"
    assert await storage.remove_admin(OTHER_ID) == "missing"
    assert not await storage.is_admin(OTHER_ID)


async def test_cannot_remove_last_admin(storage: Storage):
    assert await storage.admin_ids() == [AUTHOR_ID]
    assert await storage.remove_admin(AUTHOR_ID) == "last"
    assert await storage.is_admin(AUTHOR_ID)


async def test_find_user_by_username(storage: Storage):
    await storage.add(user_id=OTHER_ID, username="Bob", kind="text", text="x", chat_id=OTHER_ID, message_id=1)
    assert await storage.find_user("@bob") == OTHER_ID
    assert await storage.find_user("nobody") is None


# --- bot commands ---

async def test_admin_adds_admin_by_id(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text=f"/addadmin {OTHER_ID}"))
    assert await storage.is_admin(OTHER_ID)
    assert session.sent_to(AUTHOR_ID) == [texts.ADMIN_ADDED.format(who=f"id {OTHER_ID}")]
    [menu] = session.calls(SetMyCommands)
    assert menu.scope.chat_id == OTHER_ID and "addadmin" in [c.command for c in menu.commands]


async def test_admin_adds_admin_by_known_username(bot, session, storage):
    await storage.add(user_id=OTHER_ID, username="bob", kind="text", text="x", chat_id=OTHER_ID, message_id=1)
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text="/addadmin @bob"))
    assert await storage.is_admin(OTHER_ID)
    assert session.sent_to(AUTHOR_ID) == [texts.ADMIN_ADDED.format(who=f"@bob (id {OTHER_ID})")]


async def test_unknown_username_refused(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text="/addadmin @ghost"))
    assert session.sent_to(AUTHOR_ID) == [texts.ADMIN_UNKNOWN_USER]


async def test_addadmin_without_argument_shows_usage(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text="/addadmin"))
    assert session.sent_to(AUTHOR_ID) == [texts.ADD_ADMIN_USAGE]


async def test_admin_removes_admin(bot, session, storage):
    await storage.add_admin(OTHER_ID, "bob", added_by=AUTHOR_ID)
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text=f"/removeadmin {OTHER_ID}"))
    assert not await storage.is_admin(OTHER_ID)
    assert session.sent_to(AUTHOR_ID) == [texts.ADMIN_REMOVED.format(who=f"@bob (id {OTHER_ID})")]
    [reset] = session.calls(DeleteMyCommands)
    assert reset.scope.chat_id == OTHER_ID


async def test_last_admin_not_removed(bot, session, storage):
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text=f"/removeadmin {AUTHOR_ID}"))
    assert await storage.is_admin(AUTHOR_ID)
    assert session.sent_to(AUTHOR_ID) == [texts.ADMIN_LAST]


async def test_non_admin_cannot_manage_admins(bot, session, storage):
    dp = _dp(storage)
    for command in (f"/addadmin {VIEWER_ID}", f"/removeadmin {AUTHOR_ID}", "/admins"):
        await dp.feed_update(bot, make_update(user_id=VIEWER_ID, text=command))
    assert not await storage.is_admin(VIEWER_ID)
    assert await storage.is_admin(AUTHOR_ID)
    assert session.sent_to(VIEWER_ID) == [texts.NOT_ADMIN] * 3


async def test_admins_list(bot, session, storage):
    await storage.add_admin(OTHER_ID, "bob", added_by=AUTHOR_ID)
    await _dp(storage).feed_update(bot, make_update(user_id=AUTHOR_ID, text="/admins"))
    [reply] = session.sent_to(AUTHOR_ID)
    assert f"id {AUTHOR_ID}" in reply and f"@bob (id {OTHER_ID})" in reply


async def test_new_admin_gets_summary_and_notifications(bot, session, storage):
    dp = _dp(storage)
    await dp.feed_update(bot, make_update(user_id=AUTHOR_ID, text=f"/addadmin {OTHER_ID}"))
    await dp.feed_update(bot, make_update(user_id=VIEWER_ID, text="идея"))
    assert any("идея" in t for t in session.sent_to(OTHER_ID))
    assert any("идея" in t for t in session.sent_to(AUTHOR_ID))
    await dp.feed_update(bot, make_update(user_id=OTHER_ID, text="/summary"))
    assert session.sent_to(OTHER_ID)[-1] == "Темы: ИИ (2)"
