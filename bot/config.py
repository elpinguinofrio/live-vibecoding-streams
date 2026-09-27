import os
from pathlib import Path

from dotenv import dotenv_values
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

POINTER_VAR = "SECRETS_ENV_FILES"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr
    groq_api_key: SecretStr
    author_user_id: int | None = None  # legacy single admin; merged into admin_user_ids
    # First-run admins (comma-separated ids). After that the list lives in the DB, managed via /addadmin, /removeadmin
    admin_user_ids: Annotated[list[int], NoDecode] = []
    groq_summary_model: str
    groq_stt_model: str = "whisper-large-v3"
    # Local disk on purpose: SQLite WAL/locking is unsafe on network shares (the repo lives on SMB)
    db_path: str = "~/.local/share/streaming-poll-tg-bot/suggestions.db"
    groq_timeout_s: float = 20.0
    groq_max_retries: int = 2
    stt_concurrency: int = 4
    stt_timeout_s: float = 30.0
    summary_timeout_s: float = 90.0
    max_voice_bytes: int = 19 * 1024 * 1024

    @field_validator("author_user_id", mode="before")
    @classmethod
    def _blank_author_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(",", " ").split()]
        return value

    def seed_admin_ids(self) -> list[int]:
        ids = [self.author_user_id] if self.author_user_id is not None else []
        return list(dict.fromkeys([*ids, *self.admin_user_ids]))


def _pointer_files(local_env: Path) -> list[Path]:
    raw = os.environ.get(POINTER_VAR) or dotenv_values(local_env).get(POINTER_VAR) or ""
    files = []
    for item in filter(None, (part.strip() for part in raw.split(":"))):
        path = Path(os.path.expanduser(item))
        if not path.is_absolute():
            path = local_env.parent / path
        if not path.is_file():
            raise FileNotFoundError(f"{POINTER_VAR} entry not found: {path}")
        files.append(path)
    return files


def load_settings(local_env: str | Path = ".env") -> Settings:
    """Load settings from files listed in SECRETS_ENV_FILES, then the local .env (local wins)."""
    local_env = Path(local_env).resolve()
    env_files = [*_pointer_files(local_env)]
    if local_env.is_file():
        env_files.append(local_env)
    return Settings(_env_file=tuple(env_files) or None)
