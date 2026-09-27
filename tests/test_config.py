from bot.config import Settings

SECRET_TOKEN = "123456:SUPER-SECRET"
SECRET_KEY = "gsk_SECRET"


def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", SECRET_TOKEN)
    monkeypatch.setenv("GROQ_API_KEY", SECRET_KEY)
    monkeypatch.setenv("AUTHOR_USER_ID", "777")
    monkeypatch.setenv("GROQ_SUMMARY_MODEL", "some/model")


def test_settings_load_from_env(monkeypatch):
    _env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.author_user_id == 777
    assert s.groq_summary_model == "some/model"
    assert s.groq_stt_model == "whisper-large-v3"
    assert s.db_path == "~/.local/share/streaming-poll-tg-bot/suggestions.db"
    assert s.telegram_bot_token.get_secret_value() == SECRET_TOKEN


def test_secrets_hidden_in_repr(monkeypatch):
    _env(monkeypatch)
    s = Settings(_env_file=None)
    assert SECRET_TOKEN not in repr(s) and SECRET_TOKEN not in str(s)
    assert SECRET_KEY not in repr(s) and SECRET_KEY not in str(s)


def _clear_env(monkeypatch):
    for name in ("TELEGRAM_BOT_TOKEN", "GROQ_API_KEY", "AUTHOR_USER_ID", "GROQ_SUMMARY_MODEL", "SECRETS_ENV_FILES"):
        monkeypatch.delenv(name, raising=False)


def test_load_settings_follows_pointer_files(tmp_path, monkeypatch):
    from bot.config import load_settings

    _clear_env(monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "bot.env").write_text(f"TELEGRAM_BOT_TOKEN={SECRET_TOKEN}\n")
    home = tmp_path / "home"
    home.mkdir()
    (home / "keys.env").write_text(f"GROQ_API_KEY={SECRET_KEY}\nOTHER_KEY=x\n")
    monkeypatch.setenv("HOME", str(home))
    (project / ".env").write_text(
        "SECRETS_ENV_FILES=../secrets/bot.env:~/keys.env\nAUTHOR_USER_ID=42\nGROQ_SUMMARY_MODEL=m\n"
    )
    s = load_settings(project / ".env")
    assert s.telegram_bot_token.get_secret_value() == SECRET_TOKEN
    assert s.groq_api_key.get_secret_value() == SECRET_KEY
    assert s.author_user_id == 42


def test_local_env_overrides_pointer_files(tmp_path, monkeypatch):
    from bot.config import load_settings

    _clear_env(monkeypatch)
    (tmp_path / "shared.env").write_text(f"TELEGRAM_BOT_TOKEN=shared\nGROQ_API_KEY={SECRET_KEY}\nAUTHOR_USER_ID=1\n")
    (tmp_path / ".env").write_text("SECRETS_ENV_FILES=shared.env\nAUTHOR_USER_ID=2\nGROQ_SUMMARY_MODEL=m\nTELEGRAM_BOT_TOKEN=local\n")
    s = load_settings(tmp_path / ".env")
    assert s.telegram_bot_token.get_secret_value() == "local"
    assert s.author_user_id == 2


def test_missing_pointer_file_names_path_only(tmp_path, monkeypatch):
    import pytest
    from bot.config import load_settings

    _clear_env(monkeypatch)
    (tmp_path / ".env").write_text("SECRETS_ENV_FILES=nope.env\n")
    with pytest.raises(FileNotFoundError, match="nope.env"):
        load_settings(tmp_path / ".env")


def test_author_id_optional_until_known(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", SECRET_TOKEN)
    monkeypatch.setenv("GROQ_API_KEY", SECRET_KEY)
    monkeypatch.setenv("GROQ_SUMMARY_MODEL", "m")
    monkeypatch.setenv("AUTHOR_USER_ID", "")
    assert Settings(_env_file=None).author_user_id is None


def test_db_default_is_local_home_path(monkeypatch):
    _env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.db_path.startswith("~/")


def test_admin_ids_comma_separated(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("ADMIN_USER_IDS", "6901813140, 188200615")
    assert Settings(_env_file=None).admin_user_ids == [6901813140, 188200615]


def test_admin_ids_default_empty(monkeypatch):
    _env(monkeypatch)
    monkeypatch.delenv("ADMIN_USER_IDS", raising=False)
    assert Settings(_env_file=None).admin_user_ids == []
