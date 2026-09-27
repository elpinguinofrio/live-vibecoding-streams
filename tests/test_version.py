import subprocess
import tomllib
from pathlib import Path

from bot.handlers import create_dispatcher
from bot.version import get_version
from tests.conftest import AUTHOR_ID, VIEWER_ID, FakeSpeech, FakeSummarizer, make_update

ROOT = Path(__file__).resolve().parents[1]


def _expected_package_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_version_reads_pyproject_at_runtime():
    assert get_version().startswith(_expected_package_version())


def test_version_includes_git_commit_when_available():
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    version = get_version()
    if head.returncode == 0:
        assert head.stdout.strip() in version
    else:
        assert version == _expected_package_version()


async def test_version_command_replies_for_anyone_and_is_not_saved(bot, session, storage):
    dp = create_dispatcher(storage=storage, speech=FakeSpeech(), summarizer=FakeSummarizer())
    await dp.feed_update(bot, make_update(user_id=VIEWER_ID, text="/version"))
    assert session.sent_texts() == [f"Версия: {get_version()}"]
    assert await storage.count() == 0
