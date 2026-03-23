from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


PROJECTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    workspace_path TEXT PRIMARY KEY,
    channel_name   TEXT NOT NULL,
    channel_id     TEXT,
    default_model_override TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_path  TEXT NOT NULL REFERENCES projects(workspace_path),
    channel_id      TEXT NOT NULL,
    thread_ts       TEXT NOT NULL,
    cursor_chat_id  TEXT NOT NULL,
    pid             INTEGER,
    is_active       BOOLEAN DEFAULT 0,
    status          TEXT DEFAULT 'idle'
                    CHECK(status IN ('idle', 'running', 'completed', 'failed', 'timeout')),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel_id, thread_ts)
);
"""

TRANSCRIPT_SYNC_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcript_sync (
    transcript_file TEXT PRIMARY KEY,
    workspace_path  TEXT NOT NULL,
    channel_id      TEXT,
    thread_ts       TEXT,
    last_line_read  INTEGER DEFAULT 0
);
"""

HOOK_CONVERSATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS hook_conversations (
    workspace_path  TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    channel_id      TEXT NOT NULL,
    thread_ts       TEXT NOT NULL,
    cursor_chat_id  TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(workspace_path, conversation_id)
);
"""

SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

LAST_ACTIVE_INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_sessions_workspace_active
ON sessions(workspace_path, is_active, status);
"""


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self.initialize()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def initialize(self) -> None:
        with self._lock:
            self._connection.executescript(
                "\n".join(
                    [
                        PROJECTS_SCHEMA,
                        SESSIONS_SCHEMA,
                        TRANSCRIPT_SYNC_SCHEMA,
                        HOOK_CONVERSATIONS_SCHEMA,
                        SETTINGS_SCHEMA,
                        LAST_ACTIVE_INDEX_SCHEMA,
                    ]
                )
            )
            self._connection.commit()
            self._ensure_project_model_override_column()
            self._ensure_hook_conversation_cursor_chat_id_column()
            self._ensure_session_token_columns()
            self._connection.execute(
                """
                INSERT OR IGNORE INTO settings(key, value)
                VALUES ('default_model', 'auto')
                """
            )
            self._connection.commit()

    def _ensure_project_model_override_column(self) -> None:
        cursor = self._connection.execute("PRAGMA table_info(projects)")
        columns = [str(row[1]) for row in cursor.fetchall()]
        if "default_model_override" in columns:
            return
        self._connection.execute("ALTER TABLE projects ADD COLUMN default_model_override TEXT")

    def _ensure_hook_conversation_cursor_chat_id_column(self) -> None:
        cursor = self._connection.execute("PRAGMA table_info(hook_conversations)")
        columns = [str(row[1]) for row in cursor.fetchall()]
        if "cursor_chat_id" in columns:
            return
        self._connection.execute("ALTER TABLE hook_conversations ADD COLUMN cursor_chat_id TEXT")

    def _ensure_session_token_columns(self) -> None:
        cursor = self._connection.execute("PRAGMA table_info(sessions)")
        columns = [str(row[1]) for row in cursor.fetchall()]
        for col in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if col not in columns:
                self._connection.execute(f"ALTER TABLE sessions ADD COLUMN {col} INTEGER DEFAULT 0")

    def _fetchall(self, query: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = self._connection.execute(query, args)
        return [dict(row) for row in cursor.fetchall()]

    def _fetchone(self, query: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cursor = self._connection.execute(query, args)
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def add_project(
        self,
        workspace_path: str,
        channel_name: str,
        channel_id: str | None,
        default_model_override: str | None = None,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO projects(workspace_path, channel_name, channel_id, default_model_override)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(workspace_path)
                DO UPDATE SET
                    channel_name=excluded.channel_name,
                    channel_id=excluded.channel_id,
                    default_model_override=excluded.default_model_override
                """,
                (workspace_path, channel_name, channel_id, default_model_override),
            )
            self._connection.commit()

    def update_project_channel(self, workspace_path: str, channel_name: str, channel_id: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE projects
                SET channel_name=?, channel_id=?
                WHERE workspace_path=?
                """,
                (channel_name, channel_id, workspace_path),
            )
            self._connection.commit()

    def remove_project(self, workspace_path: str) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM sessions WHERE workspace_path=?", (workspace_path,))
            self._connection.execute("DELETE FROM projects WHERE workspace_path=?", (workspace_path,))
            self._connection.commit()

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._fetchall(
                """
                SELECT workspace_path, channel_name, channel_id, default_model_override, created_at
                FROM projects
                ORDER BY workspace_path
                """
            )

    def get_project_by_workspace(self, workspace_path: str) -> dict[str, Any] | None:
        with self._lock:
            return self._fetchone(
                """
                SELECT workspace_path, channel_name, channel_id, default_model_override, created_at
                FROM projects
                WHERE workspace_path=? COLLATE NOCASE
                """,
                (workspace_path,),
            )

    def get_project_by_channel_id(self, channel_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._fetchone(
                """
                SELECT workspace_path, channel_name, channel_id, default_model_override, created_at
                FROM projects
                WHERE channel_id=?
                """,
                (channel_id,),
            )

    def get_or_create_session(
        self,
        workspace_path: str,
        channel_id: str,
        thread_ts: str,
        cursor_chat_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO sessions(workspace_path, channel_id, thread_ts, cursor_chat_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(channel_id, thread_ts)
                DO UPDATE SET
                    cursor_chat_id=excluded.cursor_chat_id,
                    last_active_at=CURRENT_TIMESTAMP
                """,
                (workspace_path, channel_id, thread_ts, cursor_chat_id),
            )
            row = self._fetchone(
                """
                SELECT *
                FROM sessions
                WHERE channel_id=? AND thread_ts=?
                """,
                (channel_id, thread_ts),
            )
            self._connection.commit()
            if row is None:
                raise RuntimeError("Failed to upsert session row")
            return row

    def get_session(self, channel_id: str, thread_ts: str) -> dict[str, Any] | None:
        with self._lock:
            return self._fetchone(
                "SELECT * FROM sessions WHERE channel_id=? AND thread_ts=?",
                (channel_id, thread_ts),
            )

    def get_session_by_cursor_chat_id(
        self,
        workspace_path: str,
        cursor_chat_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            return self._fetchone(
                """
                SELECT *
                FROM sessions
                WHERE workspace_path=? AND cursor_chat_id=?
                ORDER BY last_active_at DESC
                LIMIT 1
                """,
                (workspace_path, cursor_chat_id),
            )

    def get_active_session_for_workspace(self, workspace_path: str) -> dict[str, Any] | None:
        with self._lock:
            return self._fetchone(
                """
                SELECT *
                FROM sessions
                WHERE workspace_path=? AND is_active=1 AND status='running'
                ORDER BY last_active_at DESC
                LIMIT 1
                """,
                (workspace_path,),
            )

    def set_session_running(self, session_id: int, workspace_path: str, pid: int) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE sessions SET is_active=0 WHERE workspace_path=? AND id != ?",
                (workspace_path, session_id),
            )
            self._connection.execute(
                """
                UPDATE sessions
                SET pid=?, is_active=1, status='running', last_active_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (pid, session_id),
            )
            self._connection.commit()

    def mark_session_status(self, session_id: int, status: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE sessions
                SET status=?, is_active=0, pid=NULL, last_active_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, session_id),
            )
            self._connection.commit()

    def list_running_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._fetchall(
                "SELECT * FROM sessions WHERE status='running' OR is_active=1 ORDER BY id"
            )

    def reset_running_sessions(self) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE sessions SET is_active=0, status='idle', pid=NULL WHERE status='running' OR is_active=1"
            )
            self._connection.commit()

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._fetchall(
                """
                SELECT id, workspace_path, channel_id, thread_ts, cursor_chat_id, pid, is_active, status, last_active_at
                FROM sessions
                ORDER BY last_active_at DESC
                """
            )

    def get_transcript_state(self, transcript_file: str) -> dict[str, Any] | None:
        with self._lock:
            return self._fetchone(
                """
                SELECT transcript_file, workspace_path, channel_id, thread_ts, last_line_read
                FROM transcript_sync
                WHERE transcript_file=?
                """,
                (transcript_file,),
            )

    def upsert_transcript_state(
        self,
        transcript_file: str,
        workspace_path: str,
        channel_id: str | None,
        thread_ts: str | None,
        last_line_read: int,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO transcript_sync(transcript_file, workspace_path, channel_id, thread_ts, last_line_read)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(transcript_file)
                DO UPDATE SET
                    workspace_path=excluded.workspace_path,
                    channel_id=excluded.channel_id,
                    thread_ts=excluded.thread_ts,
                    last_line_read=excluded.last_line_read
                """,
                (transcript_file, workspace_path, channel_id, thread_ts, last_line_read),
            )
            self._connection.commit()

    def get_hook_conversation(
        self,
        workspace_path: str,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            return self._fetchone(
                """
                SELECT workspace_path, conversation_id, channel_id, thread_ts, cursor_chat_id, created_at, updated_at
                FROM hook_conversations
                WHERE workspace_path=? AND conversation_id=?
                """,
                (workspace_path, conversation_id),
            )

    def get_hook_conversation_by_thread(
        self,
        workspace_path: str,
        channel_id: str,
        thread_ts: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            return self._fetchone(
                """
                SELECT workspace_path, conversation_id, channel_id, thread_ts, cursor_chat_id, created_at, updated_at
                FROM hook_conversations
                WHERE workspace_path=? AND channel_id=? AND thread_ts=?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (workspace_path, channel_id, thread_ts),
            )

    def upsert_hook_conversation(
        self,
        workspace_path: str,
        conversation_id: str,
        channel_id: str,
        thread_ts: str,
        cursor_chat_id: str | None = None,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO hook_conversations(workspace_path, conversation_id, channel_id, thread_ts, cursor_chat_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workspace_path, conversation_id)
                DO UPDATE SET
                    channel_id=excluded.channel_id,
                    thread_ts=excluded.thread_ts,
                    cursor_chat_id=COALESCE(excluded.cursor_chat_id, hook_conversations.cursor_chat_id),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (workspace_path, conversation_id, channel_id, thread_ts, cursor_chat_id),
            )
            self._connection.commit()

    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._fetchone("SELECT value FROM settings WHERE key=?", (key,))
            if row is None:
                return None
            return str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key)
                DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
                """,
                (key, value),
            )
            self._connection.commit()

    def clear_sessions(self, workspace_path: str | None = None) -> int:
        with self._lock:
            if workspace_path is not None:
                cursor = self._connection.execute(
                    "DELETE FROM sessions WHERE workspace_path=?", (workspace_path,)
                )
            else:
                cursor = self._connection.execute("DELETE FROM sessions")
            self._connection.commit()
            return cursor.rowcount

    def clear_all(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM sessions")
            self._connection.execute("DELETE FROM transcript_sync")
            self._connection.execute("DELETE FROM hook_conversations")
            self._connection.execute("DELETE FROM projects")
            self._connection.execute("DELETE FROM settings WHERE key != 'default_model'")
            self._connection.commit()

    def list_sessions_for_project(self, workspace_path: str) -> list[dict[str, Any]]:
        with self._lock:
            return self._fetchall(
                """
                SELECT id, workspace_path, channel_id, thread_ts, cursor_chat_id,
                       pid, is_active, status, created_at, last_active_at
                FROM sessions
                WHERE workspace_path=?
                ORDER BY last_active_at DESC
                """,
                (workspace_path,),
            )

    def set_project_model_override(self, workspace_path: str, model_override: str | None) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE projects SET default_model_override=? WHERE workspace_path=?",
                (model_override, workspace_path),
            )
            self._connection.commit()
            return cursor.rowcount > 0

    def get_default_model(self) -> str:
        current = self.get_setting("default_model")
        if current is None:
            return "auto"
        return current

    def set_default_model(self, model: str) -> None:
        self.set_setting("default_model", model)

    def get_model_options_cache(
        self,
        include_expired: bool = False,
        now_ts: float | None = None,
    ) -> list[str] | None:
        raw_options = self.get_setting("model_options_json")
        raw_expires_at = self.get_setting("model_options_expires_at")
        if raw_options is None or raw_expires_at is None:
            return None
        try:
            parsed = json.loads(raw_options)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, list):
            return None
        options = [item for item in parsed if isinstance(item, str) and item]
        if not options:
            return None
        try:
            expires_at = float(raw_expires_at)
        except ValueError:
            return None
        current_ts = now_ts if now_ts is not None else time.time()
        if not include_expired and current_ts >= expires_at:
            return None
        return options

    def update_session_tokens(
        self,
        session_id: int,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE sessions
                SET prompt_tokens=?, completion_tokens=?, total_tokens=?
                WHERE id=?
                """,
                (prompt_tokens, completion_tokens, total_tokens, session_id),
            )
            self._connection.commit()

    def get_session_tokens(self, session_id: int) -> dict[str, int]:
        with self._lock:
            row = self._fetchone(
                "SELECT prompt_tokens, completion_tokens, total_tokens FROM sessions WHERE id=?",
                (session_id,),
            )
        if row is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return {
            "prompt_tokens": int(row.get("prompt_tokens") or 0),
            "completion_tokens": int(row.get("completion_tokens") or 0),
            "total_tokens": int(row.get("total_tokens") or 0),
        }

    def get_workspace_token_totals(self, workspace_path: str) -> dict[str, int]:
        with self._lock:
            row = self._fetchone(
                """
                SELECT
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM sessions
                WHERE workspace_path=?
                """,
                (workspace_path,),
            )
        if row is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return {
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "total_tokens": int(row["total_tokens"]),
        }

    def set_model_options_cache(
        self,
        options: list[str],
        ttl_seconds: int,
        now_ts: float | None = None,
    ) -> None:
        if not options:
            return
        current_ts = now_ts if now_ts is not None else time.time()
        expires_at = current_ts + max(ttl_seconds, 1)
        self.set_setting("model_options_json", json.dumps(options))
        self.set_setting("model_options_expires_at", str(expires_at))
