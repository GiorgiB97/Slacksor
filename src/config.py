from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    slack_bot_token: str
    slack_app_token: str
    db_path: Path
    session_timeout_seconds: int
    keepalive_seconds: int
    post_chunk_size: int
    polling_interval_seconds: float
    enable_ide_transcript_mirror: bool
    enable_cursor_hooks_sync: bool


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _parse_bool_env(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config(dotenv_path: Path | None = None) -> AppConfig:
    if dotenv_path is None:
        dotenv_path = Path(".env")
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path)
    else:
        load_dotenv()

    db_path = Path(os.getenv("SLACKSOR_DB_PATH", "slacksor.db")).expanduser().resolve()
    session_timeout_seconds = int(os.getenv("SLACKSOR_SESSION_TIMEOUT_SECONDS", "300"))
    keepalive_seconds = int(os.getenv("SLACKSOR_KEEPALIVE_SECONDS", "30"))
    post_chunk_size = int(os.getenv("SLACKSOR_POST_CHUNK_SIZE", "3500"))
    polling_interval_seconds = float(os.getenv("SLACKSOR_POLLING_INTERVAL_SECONDS", "1.0"))
    enable_ide_transcript_mirror = _parse_bool_env(
        os.getenv("SLACKSOR_ENABLE_IDE_TRANSCRIPT_MIRROR", "true")
    )
    enable_cursor_hooks_sync = _parse_bool_env(
        os.getenv("SLACKSOR_ENABLE_CURSOR_HOOKS_SYNC", "true")
    )

    return AppConfig(
        slack_bot_token=_require_env("SLACK_BOT_TOKEN"),
        slack_app_token=_require_env("SLACK_APP_TOKEN"),
        db_path=db_path,
        session_timeout_seconds=session_timeout_seconds,
        keepalive_seconds=keepalive_seconds,
        post_chunk_size=post_chunk_size,
        polling_interval_seconds=polling_interval_seconds,
        enable_ide_transcript_mirror=enable_ide_transcript_mirror,
        enable_cursor_hooks_sync=enable_cursor_hooks_sync,
    )
