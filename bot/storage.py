import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

KINDS = ("text", "voice", "video_note", "video", "photo")  # validated here; the table has no CHECK so kinds can grow
BUSY_TIMEOUT_MS = 5000

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        voice_path TEXT,
        voice_file_id TEXT,
        stt_model TEXT
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS suggestions_chat_message ON suggestions (chat_id, message_id)",
    """
    CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        added_by INTEGER,
        added_at TEXT NOT NULL
    )
    """,
    # Which user messages an admin's notification stands for: replies to it are relayed back to that user
    """
    CREATE TABLE IF NOT EXISTS notifications (
        admin_chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        user_chat_id INTEGER NOT NULL,
        source_message_ids TEXT NOT NULL,
        created_at TEXT NOT NULL,
        emoji TEXT,
        draft TEXT,
        PRIMARY KEY (admin_chat_id, message_id)
    )
    """,
    # Voice/circle/video waiting for transcription; survives restarts, deleted once saved
    """
    CREATE TABLE IF NOT EXISTS media_jobs (
        chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        username TEXT,
        sender TEXT NOT NULL,
        kind TEXT NOT NULL,
        caption TEXT NOT NULL,
        file_id TEXT NOT NULL,
        file_unique_id TEXT NOT NULL,
        duration INTEGER,
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, message_id)
    )
    """,
)
_OUTGOING = """
    CREATE TABLE IF NOT EXISTS outgoing (
        viewer_chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        source_message_ids TEXT NOT NULL,
        preview TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (viewer_chat_id, message_id)
    )
"""  # admin answers the bot sent to viewers: a viewer's reaction on one is reported under its notification
_JOB_COLUMNS = ("chat_id", "message_id", "user_id", "username", "sender", "kind", "caption", "file_id",
                "file_unique_id", "duration", "attempts")
# Columns added after v0; ensured on open so older databases keep working (voice_* also hold video/photo ids)
_ADDED_COLUMNS = {
    "suggestions": {"voice_path": "TEXT", "voice_file_id": "TEXT", "stt_model": "TEXT"},
    "notifications": {"emoji": "TEXT", "draft": "TEXT"},
    "outgoing": {"admin_chat_id": "INTEGER", "admin_message_id": "INTEGER"},
}
_COLUMNS = "id, user_id, username, kind, text, chat_id, message_id, created_at, voice_path, voice_file_id, stt_model"


@dataclass(frozen=True)
class Suggestion:
    id: int
    user_id: int
    username: str | None
    kind: str
    text: str
    chat_id: int
    message_id: int
    created_at: str
    voice_path: str | None = None
    voice_file_id: str | None = None
    stt_model: str | None = None


@dataclass(frozen=True)
class Notification:
    user_chat_id: int
    source_message_ids: list[int]
    emoji: str | None = None
    draft: str | None = None


@dataclass(frozen=True)
class Admin:
    user_id: int
    username: str | None
    added_by: int | None
    added_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    """SQLite repository. Keep the file on local disk: WAL does not work on network filesystems."""

    def __init__(self, path: str) -> None:
        self.path = os.path.expanduser(path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        await self._db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        await self._db.execute("PRAGMA journal_mode = WAL")
        for statement in (*_SCHEMA, _OUTGOING):
            await self._db.execute(statement)
        for table, columns in _ADDED_COLUMNS.items():
            existing = {row[1] for row in await (await self._db.execute(f"PRAGMA table_info({table})")).fetchall()}
            for column, column_type in columns.items():
                if column not in existing:
                    await self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        await self._db.commit()
        await self._drop_kind_check()

    async def _drop_kind_check(self) -> None:
        """v0 tables had CHECK (kind IN ('text', 'voice')); SQLite can't alter a CHECK, so rebuild the table once."""
        cur = await self._db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'suggestions'")
        (sql,) = await cur.fetchone()
        if "CHECK" not in sql:
            return
        create = _SCHEMA[0].replace("IF NOT EXISTS suggestions", "suggestions_new")
        await self._db.executescript(
            f"BEGIN IMMEDIATE; {create};"
            f" INSERT INTO suggestions_new ({_COLUMNS}) SELECT {_COLUMNS} FROM suggestions;"
            " DROP TABLE suggestions; ALTER TABLE suggestions_new RENAME TO suggestions;"
            f" {_SCHEMA[1]}; COMMIT;"
        )

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("storage is not open")
        return self._db

    async def add(self, *, user_id: int, username: str | None, kind: str, text: str,
                  chat_id: int, message_id: int, voice_path: str | None = None,
                  voice_file_id: str | None = None, stt_model: str | None = None) -> int | None:
        """Insert a suggestion; returns its id, or None if this message was already stored."""
        if kind not in KINDS:
            raise ValueError(f"unknown kind: {kind}")
        text = text.strip()
        if not text:
            raise ValueError("empty suggestion text")
        db = self._conn()
        cur = await db.execute(
            "INSERT INTO suggestions (user_id, username, kind, text, chat_id, message_id, created_at,"
            " voice_path, voice_file_id, stt_model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (chat_id, message_id) DO NOTHING",
            (user_id, username, kind, text, chat_id, message_id, _now(),
             voice_path, voice_file_id, stt_model),
        )
        await db.commit()
        return cur.lastrowid if cur.rowcount == 1 else None

    async def list_all(self) -> list[Suggestion]:
        cur = await self._conn().execute(
            f"SELECT {_COLUMNS} FROM suggestions ORDER BY id"
        )
        return [Suggestion(*row) for row in await cur.fetchall()]

    async def count(self) -> int:
        cur = await self._conn().execute("SELECT COUNT(*) FROM suggestions")
        (n,) = await cur.fetchone()
        return n

    async def known_username(self, user_id: int) -> str | None:
        """Latest name this user was seen with in suggestions."""
        cur = await self._conn().execute(
            "SELECT username FROM suggestions WHERE user_id = ? AND username IS NOT NULL ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def find_user(self, username: str) -> int | None:
        """Resolve @username to an id among users the bot has seen (Bot API cannot look up strangers)."""
        name = username.removeprefix("@")
        cur = await self._conn().execute(
            "SELECT user_id FROM (SELECT user_id, username, id AS o FROM suggestions"
            " UNION ALL SELECT user_id, username, 1e18 AS o FROM admins)"
            " WHERE username = ? COLLATE NOCASE ORDER BY o DESC LIMIT 1",
            (name,),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def seed_admins(self, user_ids: list[int]) -> bool:
        """First run only: fill an empty admins table. Later changes are made in the bot and survive restarts."""
        db = self._conn()
        (n,) = await (await db.execute("SELECT COUNT(*) FROM admins")).fetchone()
        if n or not user_ids:
            return False
        for user_id in dict.fromkeys(user_ids):
            await db.execute("INSERT INTO admins (user_id, username, added_by, added_at) VALUES (?, ?, NULL, ?)",
                             (user_id, await self.known_username(user_id), _now()))
        await db.commit()
        return True

    async def list_admins(self) -> list[Admin]:
        cur = await self._conn().execute("SELECT user_id, username, added_by, added_at FROM admins ORDER BY added_at, user_id")
        return [Admin(*row) for row in await cur.fetchall()]

    async def admin_ids(self) -> list[int]:
        return [a.user_id for a in await self.list_admins()]

    async def is_admin(self, user_id: int) -> bool:
        cur = await self._conn().execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return await cur.fetchone() is not None

    async def get_admin(self, user_id: int) -> Admin | None:
        cur = await self._conn().execute(
            "SELECT user_id, username, added_by, added_at FROM admins WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return Admin(*row) if row else None

    async def add_admin(self, user_id: int, username: str | None, *, added_by: int) -> bool:
        """Returns False if already an admin."""
        db = self._conn()
        cur = await db.execute(
            "INSERT INTO admins (user_id, username, added_by, added_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (user_id) DO NOTHING",
            (user_id, username, added_by, _now()),
        )
        await db.commit()
        return cur.rowcount == 1

    async def remove_admin(self, user_id: int) -> str:
        """Returns "removed", "missing", or "last" (the last admin is never removed)."""
        db = self._conn()
        cur = await db.execute(
            "DELETE FROM admins WHERE user_id = ? AND (SELECT COUNT(*) FROM admins) > 1", (user_id,))
        await db.commit()
        if cur.rowcount == 1:
            return "removed"
        return "last" if await self.is_admin(user_id) else "missing"

    async def add_notification(self, *, admin_chat_id: int, message_id: int, user_chat_id: int,
                               source_message_ids: list[int], emoji: str | None = None,
                               draft: str | None = None) -> None:
        db = self._conn()
        await db.execute(
            "INSERT OR REPLACE INTO notifications (admin_chat_id, message_id, user_chat_id, source_message_ids,"
            " created_at, emoji, draft) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (admin_chat_id, message_id, user_chat_id, json.dumps(source_message_ids), _now(), emoji, draft),
        )
        await db.commit()

    async def get_notification(self, admin_chat_id: int, message_id: int) -> Notification | None:
        cur = await self._conn().execute(
            "SELECT user_chat_id, source_message_ids, emoji, draft FROM notifications"
            " WHERE admin_chat_id = ? AND message_id = ?",
            (admin_chat_id, message_id),
        )
        row = await cur.fetchone()
        return Notification(row[0], json.loads(row[1]), row[2], row[3]) if row else None

    async def latest_notification(self, admin_chat_id: int) -> Notification | None:
        """The last notification this admin got: where a plain (non-reply) admin message goes."""
        cur = await self._conn().execute(
            "SELECT user_chat_id, source_message_ids, emoji, draft FROM notifications WHERE admin_chat_id = ?"
            " ORDER BY created_at DESC, message_id DESC LIMIT 1",
            (admin_chat_id,),
        )
        row = await cur.fetchone()
        return Notification(row[0], json.loads(row[1]), row[2], row[3]) if row else None

    async def enqueue_job(self, job: dict) -> None:
        db = self._conn()
        await db.execute(
            f"INSERT OR IGNORE INTO media_jobs ({', '.join(_JOB_COLUMNS)}, created_at)"
            f" VALUES ({', '.join('?' * len(_JOB_COLUMNS))}, ?)",
            (*(job[c] for c in _JOB_COLUMNS), _now()),
        )
        await db.commit()

    async def set_job_attempts(self, chat_id: int, message_id: int, attempts: int) -> None:
        db = self._conn()
        await db.execute("UPDATE media_jobs SET attempts = ? WHERE chat_id = ? AND message_id = ?",
                         (attempts, chat_id, message_id))
        await db.commit()

    async def delete_job(self, chat_id: int, message_id: int) -> None:
        db = self._conn()
        await db.execute("DELETE FROM media_jobs WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))
        await db.commit()

    async def pending_jobs(self) -> list[dict]:
        cur = await self._conn().execute(f"SELECT {', '.join(_JOB_COLUMNS)} FROM media_jobs ORDER BY created_at")
        return [dict(zip(_JOB_COLUMNS, row)) for row in await cur.fetchall()]

    async def list_by_user(self, user_id: int) -> list[Suggestion]:
        cur = await self._conn().execute(f"SELECT {_COLUMNS} FROM suggestions WHERE user_id = ? ORDER BY id", (user_id,))
        return [Suggestion(*row) for row in await cur.fetchall()]

    async def add_outgoing(self, *, viewer_chat_id: int, message_id: int, source_message_ids: list[int],
                           preview: str, admin_chat_id: int, admin_message_id: int) -> None:
        """admin_message_id: where a viewer's reaction is mirrored — the admin's own answer, or the notification
        whose draft button sent it."""
        db = self._conn()
        await db.execute(
            "INSERT OR REPLACE INTO outgoing (viewer_chat_id, message_id, source_message_ids, preview, created_at,"
            " admin_chat_id, admin_message_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (viewer_chat_id, message_id, json.dumps(source_message_ids), preview, _now(), admin_chat_id,
             admin_message_id),
        )
        await db.commit()

    async def get_outgoing(self, viewer_chat_id: int, message_id: int) -> dict | None:
        cur = await self._conn().execute(
            "SELECT source_message_ids, preview, admin_chat_id, admin_message_id FROM outgoing"
            " WHERE viewer_chat_id = ? AND message_id = ?",
            (viewer_chat_id, message_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return {"source_message_ids": json.loads(row[0]), "preview": row[1], "admin_chat_id": row[2],
                "admin_message_id": row[3]}
