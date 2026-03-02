from __future__ import annotations

from pathlib import Path

import pytest

from config import load_config


def test_load_config_from_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SLACK_BOT_TOKEN=xoxb-test",
                "SLACK_APP_TOKEN=xapp-test",
                "SLACKSOR_DB_PATH=./local.db",
                "SLACKSOR_SESSION_TIMEOUT_SECONDS=111",
                "SLACKSOR_KEEPALIVE_SECONDS=9",
                "SLACKSOR_POST_CHUNK_SIZE=4000",
                "SLACKSOR_POLLING_INTERVAL_SECONDS=0.5",
                "SLACKSOR_ENABLE_IDE_TRANSCRIPT_MIRROR=true",
                "SLACKSOR_ENABLE_CURSOR_HOOKS_SYNC=true",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    config = load_config(dotenv_path=env_file)
    assert config.slack_bot_token == "xoxb-test"
    assert config.slack_app_token == "xapp-test"
    assert config.db_path == (tmp_path / "local.db").resolve()
    assert config.session_timeout_seconds == 111
    assert config.keepalive_seconds == 9
    assert config.post_chunk_size == 4000
    assert config.polling_interval_seconds == 0.5
    assert config.enable_ide_transcript_mirror is True
    assert config.enable_cursor_hooks_sync is True


def test_missing_required_variables_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    with pytest.raises(ValueError):
        load_config(dotenv_path=env_file)
