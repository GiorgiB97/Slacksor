from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import AppConfig
from db import Database


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        db_path=tmp_path / "slacksor.db",
        session_timeout_seconds=5,
        keepalive_seconds=1,
        post_chunk_size=50,
        polling_interval_seconds=0.01,
        enable_ide_transcript_mirror=False,
        enable_cursor_hooks_sync=True,
    )


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    yield db
    db.close()
